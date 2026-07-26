/*
 * Copyright (c) 2026
 * SPDX-License-Identifier: BSD-3-Clause
 */

#define _POSIX_C_SOURCE 200809L

#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

static volatile sig_atomic_t timed_out;
static volatile sig_atomic_t command_pid;

static void handle_timeout(int signal_number)
{
	(void)signal_number;
	timed_out = 1;
	if (command_pid > 0)
		kill(command_pid, SIGKILL);
}

static uint64_t monotonic_raw_ns(void)
{
	struct timespec value;

	if (clock_gettime(CLOCK_MONOTONIC_RAW, &value) != 0)
		return 0;
	return (uint64_t)value.tv_sec * 1000000000ULL + value.tv_nsec;
}

static int usage(const char *program)
{
	fprintf(stderr,
		"usage: %s --elapsed-ns FILE [--timeout-seconds N] "
		"-- COMMAND [ARG ...]\n",
		program);
	return 2;
}

int main(int argc, char **argv)
{
	const char *output_path;
	char **command;
	uint64_t before;
	uint64_t after;
	FILE *output;
	pid_t child;
	int status;
	unsigned long timeout_seconds = 0;
	char *end;

	if (argc < 5 || strcmp(argv[1], "--elapsed-ns") != 0)
		return usage(argv[0]);
	output_path = argv[2];
	if (strcmp(argv[3], "--timeout-seconds") == 0) {
		if (argc < 7)
			return usage(argv[0]);
		errno = 0;
		timeout_seconds = strtoul(argv[4], &end, 10);
		if (errno || *end || timeout_seconds == 0 ||
		    strcmp(argv[5], "--") != 0)
			return usage(argv[0]);
		command = &argv[6];
	} else {
		if (strcmp(argv[3], "--") != 0)
			return usage(argv[0]);
		command = &argv[4];
	}

	before = monotonic_raw_ns();
	if (!before) {
		perror("clock_gettime");
		return 2;
	}

	child = fork();
	if (child < 0) {
		perror("fork");
		return 2;
	}
	if (child == 0) {
		execvp(command[0], command);
		perror("execvp");
		_exit(127);
	}
	command_pid = child;
	if (timeout_seconds) {
		signal(SIGALRM, handle_timeout);
		alarm((unsigned int)timeout_seconds);
	}

	for (;;) {
		if (waitpid(child, &status, 0) >= 0)
			break;
		if (errno != EINTR) {
			perror("waitpid");
			return 2;
		}
	}
	alarm(0);

	after = monotonic_raw_ns();
	if (!after) {
		perror("clock_gettime");
		return 2;
	}
	output = fopen(output_path, "w");
	if (!output) {
		perror(output_path);
		return 2;
	}
	fprintf(output, "%llu\n", (unsigned long long)(after - before));
	if (fclose(output) != 0) {
		perror(output_path);
		return 2;
	}

	if (timed_out)
		return 124;
	if (WIFEXITED(status))
		return WEXITSTATUS(status);
	if (WIFSIGNALED(status))
		return 128 + WTERMSIG(status);
	return 2;
}
