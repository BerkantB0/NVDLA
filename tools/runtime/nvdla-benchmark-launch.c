/*
 * Copyright (c) 2026
 * SPDX-License-Identifier: BSD-3-Clause
 */

#define _GNU_SOURCE

#include <errno.h>
#include <sched.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
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
		"usage: %s --elapsed-ns FILE [--interval FILE] [--rusage FILE] [--cpu N] "
		"[--timeout-seconds N] -- COMMAND [ARG ...]\n",
		program);
	return 2;
}

static uint64_t timeval_ns(const struct timeval *value)
{
	return (uint64_t)value->tv_sec * 1000000000ULL +
	       (uint64_t)value->tv_usec * 1000ULL;
}

static int write_rusage(const char *path, const struct rusage *value, int cpu)
{
	FILE *output;

	if (!path)
		return 0;
	output = fopen(path, "w");
	if (!output) {
		perror(path);
		return -1;
	}
	fprintf(output, "schema_version=1\n");
	if (cpu >= 0)
		fprintf(output, "cpu_affinity=%d\n", cpu);
	else
		fprintf(output, "cpu_affinity=none\n");
	fprintf(output, "user_time_ns=%llu\n",
		(unsigned long long)timeval_ns(&value->ru_utime));
	fprintf(output, "system_time_ns=%llu\n",
		(unsigned long long)timeval_ns(&value->ru_stime));
	fprintf(output, "minor_page_faults=%ld\n", value->ru_minflt);
	fprintf(output, "major_page_faults=%ld\n", value->ru_majflt);
	fprintf(output, "voluntary_context_switches=%ld\n", value->ru_nvcsw);
	fprintf(output, "involuntary_context_switches=%ld\n", value->ru_nivcsw);
	fprintf(output, "cpu_migrations=unavailable\n");
	if (fclose(output) != 0) {
		perror(path);
		return -1;
	}
	return 0;
}

static int write_interval(const char *path, uint64_t before, uint64_t after)
{
	FILE *output;

	if (!path)
		return 0;
	output = fopen(path, "w");
	if (!output) {
		perror(path);
		return -1;
	}
	fprintf(output, "schema_version=1\n");
	fprintf(output, "clock=CLOCK_MONOTONIC_RAW\n");
	fprintf(output, "start_ns=%llu\n", (unsigned long long)before);
	fprintf(output, "end_ns=%llu\n", (unsigned long long)after);
	fprintf(output, "elapsed_ns=%llu\n",
		(unsigned long long)(after - before));
	if (fclose(output) != 0) {
		perror(path);
		return -1;
	}
	return 0;
}

int main(int argc, char **argv)
{
	const char *output_path;
	const char *interval_path = NULL;
	const char *rusage_path = NULL;
	char **command;
	uint64_t before;
	uint64_t after;
	FILE *output;
	pid_t child;
	int status;
	int cpu = -1;
	int argument;
	unsigned long timeout_seconds = 0;
	long parsed;
	char *end;
	struct rusage child_usage;

	if (argc < 5 || strcmp(argv[1], "--elapsed-ns") != 0)
		return usage(argv[0]);
	output_path = argv[2];
	argument = 3;
	while (argument < argc && strcmp(argv[argument], "--") != 0) {
		if (strcmp(argv[argument], "--interval") == 0) {
			if (++argument >= argc)
				return usage(argv[0]);
			interval_path = argv[argument++];
		} else if (strcmp(argv[argument], "--rusage") == 0) {
			if (++argument >= argc)
				return usage(argv[0]);
			rusage_path = argv[argument++];
		} else if (strcmp(argv[argument], "--cpu") == 0) {
			if (++argument >= argc)
				return usage(argv[0]);
			errno = 0;
			parsed = strtol(argv[argument++], &end, 10);
			if (errno || *end || parsed < 0 || parsed >= CPU_SETSIZE)
				return usage(argv[0]);
			cpu = (int)parsed;
		} else if (strcmp(argv[argument], "--timeout-seconds") == 0) {
			if (++argument >= argc)
				return usage(argv[0]);
			errno = 0;
			timeout_seconds = strtoul(argv[argument++], &end, 10);
			if (errno || *end || timeout_seconds == 0)
				return usage(argv[0]);
		} else {
			return usage(argv[0]);
		}
	}
	if (argument >= argc - 1)
		return usage(argv[0]);
	command = &argv[argument + 1];

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
		if (cpu >= 0) {
			cpu_set_t affinity;

			CPU_ZERO(&affinity);
			CPU_SET(cpu, &affinity);
			if (sched_setaffinity(0, sizeof(affinity), &affinity) != 0) {
				perror("sched_setaffinity");
				_exit(126);
			}
		}
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
		if (wait4(child, &status, 0, &child_usage) >= 0)
			break;
		if (errno != EINTR) {
			perror("wait4");
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
	if (write_interval(interval_path, before, after) != 0)
		return 2;
	if (write_rusage(rusage_path, &child_usage, cpu) != 0)
		return 2;

	if (timed_out)
		return 124;
	if (WIFEXITED(status))
		return WEXITSTATUS(status);
	if (WIFSIGNALED(status))
		return 128 + WTERMSIG(status);
	return 2;
}
