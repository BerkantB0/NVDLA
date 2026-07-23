#define _POSIX_C_SOURCE 200809L

#include <arpa/inet.h>
#include <errno.h>
#include <netinet/in.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/types.h>
#include <unistd.h>

#define DEFAULT_HOST "127.0.0.1"
#define DEFAULT_PORT 6666
#define DEFAULT_TIMEOUT 120
#define CONNECT_RETRIES 30
#define MAX_FRAME_SIZE (64U * 1024U * 1024U)

struct frame {
	uint8_t *data;
	size_t size;
};

static int write_all(int fd, const void *buffer, size_t size)
{
	const uint8_t *cursor = buffer;

	while (size > 0) {
		ssize_t written = send(fd, cursor, size, 0);

		if (written < 0 && errno == EINTR)
			continue;
		if (written <= 0)
			return -1;
		cursor += written;
		size -= (size_t)written;
	}
	return 0;
}

static int read_all(int fd, void *buffer, size_t size)
{
	uint8_t *cursor = buffer;

	while (size > 0) {
		ssize_t received = recv(fd, cursor, size, 0);

		if (received < 0 && errno == EINTR)
			continue;
		if (received <= 0)
			return -1;
		cursor += received;
		size -= (size_t)received;
	}
	return 0;
}

static int send_frame(int fd, const void *payload, size_t size)
{
	char header[32];
	int length = snprintf(header, sizeof(header), "%zu\n", size);

	if (length <= 0 || (size_t)length >= sizeof(header))
		return -1;
	if (write_all(fd, header, (size_t)length) != 0)
		return -1;
	return write_all(fd, payload, size);
}

static int send_text(int fd, const char *text)
{
	return send_frame(fd, text, strlen(text));
}

static int recv_frame(int fd, struct frame *frame)
{
	char header[32];
	size_t used = 0;
	char *end = NULL;
	unsigned long long parsed;

	memset(frame, 0, sizeof(*frame));
	while (used + 1 < sizeof(header)) {
		char byte;
		ssize_t received = recv(fd, &byte, 1, 0);

		if (received < 0 && errno == EINTR)
			continue;
		if (received <= 0)
			return -1;
		if (byte == '\n')
			break;
		if (byte < '0' || byte > '9')
			return -1;
		header[used++] = byte;
	}
	if (used == 0 || used + 1 >= sizeof(header))
		return -1;
	header[used] = '\0';
	errno = 0;
	parsed = strtoull(header, &end, 10);
	if (errno != 0 || !end || *end != '\0' || parsed > MAX_FRAME_SIZE)
		return -1;

	frame->size = (size_t)parsed;
	frame->data = malloc(frame->size + 1);
	if (!frame->data)
		return -1;
	if (read_all(fd, frame->data, frame->size) != 0) {
		free(frame->data);
		memset(frame, 0, sizeof(*frame));
		return -1;
	}
	frame->data[frame->size] = '\0';
	return 0;
}

static void free_frame(struct frame *frame)
{
	free(frame->data);
	memset(frame, 0, sizeof(*frame));
}

static bool response_has_error(const struct frame *frame)
{
	return frame->size >= 3 && strstr((const char *)frame->data, "ERR") != NULL;
}

static int read_file(const char *path, struct frame *content)
{
	FILE *file = fopen(path, "rb");
	long length;

	memset(content, 0, sizeof(*content));
	if (!file)
		return -1;
	if (fseek(file, 0, SEEK_END) != 0)
		goto fail;
	length = ftell(file);
	if (length < 0 || (unsigned long)length > MAX_FRAME_SIZE)
		goto fail;
	if (fseek(file, 0, SEEK_SET) != 0)
		goto fail;
	content->size = (size_t)length;
	content->data = malloc(content->size ? content->size : 1);
	if (!content->data)
		goto fail;
	if (content->size && fread(content->data, 1, content->size, file) != content->size)
		goto fail;
	fclose(file);
	return 0;

fail:
	fclose(file);
	free_frame(content);
	return -1;
}

static int write_file(const char *path, const struct frame *content)
{
	FILE *file = fopen(path, "wb");

	if (!file)
		return -1;
	if (content->size && fwrite(content->data, 1, content->size, file) != content->size) {
		fclose(file);
		return -1;
	}
	return fclose(file);
}

static int set_socket_timeout(int fd, int seconds)
{
	struct timeval timeout = {
		.tv_sec = seconds,
		.tv_usec = 0,
	};

	if (setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout)) != 0)
		return -1;
	return setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
}

static int connect_runtime(const char *host, unsigned int port, int timeout)
{
	struct sockaddr_in address = {
		.sin_family = AF_INET,
		.sin_port = htons((uint16_t)port),
	};

	if (inet_pton(AF_INET, host, &address.sin_addr) != 1) {
		fprintf(stderr, "invalid IPv4 runtime host: %s\n", host);
		return -1;
	}

	for (int attempt = 1; attempt <= CONNECT_RETRIES; ++attempt) {
		int fd = socket(AF_INET, SOCK_STREAM, 0);

		if (fd < 0)
			return -1;
		if (set_socket_timeout(fd, timeout) != 0) {
			close(fd);
			return -1;
		}
		if (connect(fd, (struct sockaddr *)&address, sizeof(address)) == 0)
			return fd;
		close(fd);
		if (attempt < CONNECT_RETRIES)
			sleep(1);
	}
	fprintf(stderr, "could not connect to %s:%u\n", host, port);
	return -1;
}

static int command_response(int fd, const char *command, struct frame *response)
{
	if (send_text(fd, command) != 0 || recv_frame(fd, response) != 0) {
		fprintf(stderr, "%s transport failed: %s\n", command, strerror(errno));
		return -1;
	}
	if (response_has_error(response)) {
		fprintf(stderr, "%s failed: %.*s\n", command, (int)response->size, response->data);
		return -1;
	}
	return 0;
}

static int run_protocol(int fd, const char *flatbuf_path, const char *out_dir)
{
	struct frame flatbuf = {0};
	struct frame response = {0};
	char output_path[512];
	char index_text[32];
	long outputs;
	char *end = NULL;
	int status = 1;

	if (command_response(fd, "GET_WELCOME", &response) != 0)
		goto out;
	printf("welcome=%.*s\n", (int)response.size, response.data);
	free_frame(&response);

	if (read_file(flatbuf_path, &flatbuf) != 0) {
		fprintf(stderr, "could not read flatbuffer %s: %s\n", flatbuf_path, strerror(errno));
		goto out;
	}
	if (send_text(fd, "READ_FLATBUF") != 0 || send_frame(fd, flatbuf.data, flatbuf.size) != 0) {
		fprintf(stderr, "READ_FLATBUF transport failed\n");
		goto out;
	}
	printf("read_flatbuf=%s bytes=%zu\n", flatbuf_path, flatbuf.size);
	free_frame(&flatbuf);

	if (command_response(fd, "RUN_FLATBUF", &response) != 0)
		goto out;
	printf("run_result=%.*s\n", (int)response.size, response.data);
	if (!strstr((const char *)response.data, "PASSED")) {
		fprintf(stderr, "RUN_FLATBUF did not report PASSED\n");
		goto out;
	}
	free_frame(&response);

	if (command_response(fd, "GET_NUMOUTPUTS", &response) != 0)
		goto out;
	errno = 0;
	outputs = strtol((const char *)response.data, &end, 10);
	if (errno != 0 || !end || *end != '\0' || outputs < 0 || outputs > 1024) {
		fprintf(stderr, "invalid output count: %.*s\n", (int)response.size, response.data);
		goto out;
	}
	printf("num_outputs=%ld\n", outputs);
	free_frame(&response);

	for (long index = 0; index < outputs; ++index) {
		if (send_text(fd, "GET_OUTPUT") != 0)
			goto out;
		snprintf(index_text, sizeof(index_text), "%ld", index);
		if (send_text(fd, index_text) != 0 || recv_frame(fd, &response) != 0)
			goto out;
		if (snprintf(output_path, sizeof(output_path), "%s/o_%06ld.dimg", out_dir, index)
		    >= (int)sizeof(output_path)) {
			fprintf(stderr, "output path is too long\n");
			goto out;
		}
		if (write_file(output_path, &response) != 0) {
			fprintf(stderr, "could not write %s: %s\n", output_path, strerror(errno));
			goto out;
		}
		printf("output[%ld]=%s bytes=%zu\n", index, output_path, response.size);
		free_frame(&response);
	}

	if (command_response(fd, "SHUTDOWN", &response) != 0)
		goto out;
	printf("shutdown=%.*s\n", (int)response.size, response.data);
	status = 0;

out:
	free_frame(&flatbuf);
	free_frame(&response);
	return status;
}

static int framing_self_test(void)
{
	static const uint8_t payload[] = {0x00, 0x01, 0x7f, 0x80, 0xff};
	int sockets[2] = {-1, -1};
	struct frame received = {0};
	int status = 1;

	if (socketpair(AF_UNIX, SOCK_STREAM, 0, sockets) != 0)
		goto out;
	if (send_frame(sockets[0], payload, sizeof(payload)) != 0)
		goto out;
	if (recv_frame(sockets[1], &received) != 0)
		goto out;
	if (received.size != sizeof(payload) || memcmp(received.data, payload, sizeof(payload)) != 0)
		goto out;
	status = 0;
	printf("flatbuffer framing self-test passed\n");

out:
	free_frame(&received);
	if (sockets[0] >= 0)
		close(sockets[0]);
	if (sockets[1] >= 0)
		close(sockets[1]);
	return status;
}

static void usage(const char *program)
{
	fprintf(
		stderr,
		"usage: %s --flatbuf PATH --out-dir DIR [--host IPV4] [--port N] [--timeout SEC]\n"
		"       %s --self-test\n",
		program,
		program
	);
}

int main(int argc, char **argv)
{
	const char *flatbuf = NULL;
	const char *out_dir = NULL;
	const char *host = DEFAULT_HOST;
	unsigned int port = DEFAULT_PORT;
	int timeout = DEFAULT_TIMEOUT;
	int fd;
	int status;

	if (argc == 2 && strcmp(argv[1], "--self-test") == 0)
		return framing_self_test();

	for (int index = 1; index < argc; ++index) {
		if (strcmp(argv[index], "--flatbuf") == 0 && index + 1 < argc)
			flatbuf = argv[++index];
		else if (strcmp(argv[index], "--out-dir") == 0 && index + 1 < argc)
			out_dir = argv[++index];
		else if (strcmp(argv[index], "--host") == 0 && index + 1 < argc)
			host = argv[++index];
		else if (strcmp(argv[index], "--port") == 0 && index + 1 < argc)
			port = (unsigned int)strtoul(argv[++index], NULL, 10);
		else if (strcmp(argv[index], "--timeout") == 0 && index + 1 < argc)
			timeout = (int)strtol(argv[++index], NULL, 10);
		else {
			usage(argv[0]);
			return 2;
		}
	}
	if (!flatbuf || !out_dir || port == 0 || port > 65535 || timeout <= 0) {
		usage(argv[0]);
		return 2;
	}
	if (mkdir(out_dir, 0755) != 0 && errno != EEXIST) {
		fprintf(stderr, "could not create output directory %s: %s\n", out_dir, strerror(errno));
		return 3;
	}

	fd = connect_runtime(host, port, timeout);
	if (fd < 0)
		return 4;
	status = run_protocol(fd, flatbuf, out_dir);
	close(fd);
	return status;
}
