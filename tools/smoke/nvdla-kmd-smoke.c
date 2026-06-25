/*
 * Minimal userspace smoke test for the NVDLA Linux DRM/GEM KMD path.
 *
 * This intentionally does not submit an accelerator task. It verifies that the
 * render node is usable and that the custom GEM ioctls can allocate, map, touch,
 * and destroy a buffer.
 */

#define _GNU_SOURCE

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>

#include <linux/ioctl.h>

#ifndef DRM_IOCTL_BASE
#define DRM_IOCTL_BASE 'd'
#endif

#ifndef DRM_COMMAND_BASE
#define DRM_COMMAND_BASE 0x40
#endif

#ifndef DRM_IOWR
#define DRM_IOWR(nr, type) _IOWR(DRM_IOCTL_BASE, nr, type)
#endif

#include "nvdla_ioctl.h"

#define SMOKE_BUFFER_SIZE 4096U

_Static_assert(DRM_NVDLA_SUBMIT == 0x00, "unexpected submit ioctl command");
_Static_assert(DRM_NVDLA_GEM_CREATE == 0x01, "unexpected GEM create ioctl command");
_Static_assert(DRM_NVDLA_GEM_MMAP == 0x02, "unexpected GEM mmap ioctl command");
_Static_assert(DRM_NVDLA_GEM_DESTROY == 0x03, "unexpected GEM destroy ioctl command");
_Static_assert(sizeof(struct nvdla_gem_create_args) == 16, "unexpected GEM create ABI size");
_Static_assert(sizeof(struct nvdla_gem_map_offset_args) == 16, "unexpected GEM map ABI size");
_Static_assert(sizeof(struct nvdla_gem_destroy_args) == 4, "unexpected GEM destroy ABI size");

static int find_render_node(char *path, size_t path_size)
{
	const char *override = getenv("NVDLA_DEVICE_NODE");
	DIR *dir;
	struct dirent *entry;

	if (override && override[0] != '\0') {
		if (snprintf(path, path_size, "%s", override) >= (int)path_size) {
			fprintf(stderr, "NVDLA_DEVICE_NODE is too long\n");
			return -1;
		}
		return 0;
	}

	dir = opendir("/dev/dri");
	if (!dir) {
		fprintf(stderr, "failed to open /dev/dri: %s\n", strerror(errno));
		return -1;
	}

	while ((entry = readdir(dir)) != NULL) {
		if (strncmp(entry->d_name, "renderD", strlen("renderD")) != 0)
			continue;
		if (snprintf(path, path_size, "/dev/dri/%s", entry->d_name) >= (int)path_size) {
			fprintf(stderr, "render node path is too long\n");
			closedir(dir);
			return -1;
		}
		closedir(dir);
		return 0;
	}

	closedir(dir);
	fprintf(stderr, "no /dev/dri/renderD* node found\n");
	return -1;
}

static int destroy_handle(int fd, uint32_t handle)
{
	struct nvdla_gem_destroy_args destroy_args = {
		.handle = handle,
	};

	if (handle == 0)
		return 0;

	if (ioctl(fd, DRM_IOCTL_NVDLA_GEM_DESTROY, &destroy_args) != 0) {
		fprintf(stderr, "DRM_IOCTL_NVDLA_GEM_DESTROY failed: %s\n", strerror(errno));
		return -1;
	}

	return 0;
}

int main(void)
{
	char node[256];
	struct nvdla_gem_create_args create_args = {
		.size = SMOKE_BUFFER_SIZE,
	};
	struct nvdla_gem_map_offset_args map_args = {0};
	uint8_t *mapped = MAP_FAILED;
	int fd = -1;
	int ret = 1;

	if (find_render_node(node, sizeof(node)) != 0)
		return 2;

	fd = open(node, O_RDWR | O_CLOEXEC);
	if (fd < 0) {
		fprintf(stderr, "failed to open %s: %s\n", node, strerror(errno));
		return 3;
	}

	printf("opened %s\n", node);

	if (ioctl(fd, DRM_IOCTL_NVDLA_GEM_CREATE, &create_args) != 0) {
		fprintf(stderr, "DRM_IOCTL_NVDLA_GEM_CREATE failed: %s\n", strerror(errno));
		goto out;
	}

	printf("created GEM handle=%u size=%llu\n",
	       create_args.handle, (unsigned long long)create_args.size);

	map_args.handle = create_args.handle;
	if (ioctl(fd, DRM_IOCTL_NVDLA_GEM_MMAP, &map_args) != 0) {
		fprintf(stderr, "DRM_IOCTL_NVDLA_GEM_MMAP failed: %s\n", strerror(errno));
		goto out_destroy;
	}

	printf("mmap offset=0x%llx\n", (unsigned long long)map_args.offset);

	mapped = mmap(NULL, SMOKE_BUFFER_SIZE, PROT_READ | PROT_WRITE, MAP_SHARED,
		      fd, (off_t)map_args.offset);
	if (mapped == MAP_FAILED) {
		fprintf(stderr, "mmap failed: %s\n", strerror(errno));
		goto out_destroy;
	}

	for (uint32_t i = 0; i < SMOKE_BUFFER_SIZE; ++i)
		mapped[i] = (uint8_t)(i ^ 0x5aU);

	for (uint32_t i = 0; i < SMOKE_BUFFER_SIZE; ++i) {
		uint8_t expected = (uint8_t)(i ^ 0x5aU);

		if (mapped[i] != expected) {
			fprintf(stderr, "buffer mismatch at %u: got 0x%02x expected 0x%02x\n",
				i, mapped[i], expected);
			goto out_unmap;
		}
	}

	printf("GEM mmap read/write check passed\n");
	ret = 0;

out_unmap:
	if (munmap(mapped, SMOKE_BUFFER_SIZE) != 0) {
		fprintf(stderr, "munmap failed: %s\n", strerror(errno));
		ret = 1;
	}

out_destroy:
	if (destroy_handle(fd, create_args.handle) != 0)
		ret = 1;

out:
	close(fd);
	return ret;
}
