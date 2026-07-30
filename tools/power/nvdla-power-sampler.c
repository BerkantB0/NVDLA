#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <glob.h>
#include <limits.h>
#include <sched.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define MAX_SENSORS 64
#define LABEL_SIZE 64

struct sensor {
    int fd;
    char path[PATH_MAX];
    char label[LABEL_SIZE];
    char domain[8];
};

static volatile sig_atomic_t stop_requested;

static void handle_signal(int signal_number)
{
    (void)signal_number;
    stop_requested = 1;
}

static int64_t timespec_ns(const struct timespec *value)
{
    return (int64_t)value->tv_sec * 1000000000LL + value->tv_nsec;
}

static int read_text_file(const char *path, char *buffer, size_t size)
{
    int fd;
    ssize_t count;
    size_t index;

    fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0)
        return -1;
    count = read(fd, buffer, size - 1);
    close(fd);
    if (count <= 0)
        return -1;
    buffer[count] = '\0';
    for (index = 0; index < (size_t)count; index++) {
        if (buffer[index] == '\0' || buffer[index] == '\n' ||
            buffer[index] == '\r') {
            buffer[index] = '\0';
            break;
        }
    }
    return buffer[0] == '\0' ? -1 : 0;
}

static const char *fallback_domain(const char *label)
{
    static const char *const ps_labels[] = {
        "VCCPSINTFP", "VCCPSINTLP", "VCCPSAUX", "VCCPSPLL",
        "MGTRAVCC", "MGTRAVTT", "VCCO_PSDDR_504", "VCCOPS",
        "VCCOPS3", "VCCPSDDRPLL",
    };
    static const char *const pl_labels[] = {
        "VCCINT", "VCCBRAM", "VCCAUX", "VCC1V2", "VCC3V3", "VADJ_FMC",
        "MGTAVCC", "MGTAVTT",
    };
    size_t index;

    for (index = 0; index < sizeof(ps_labels) / sizeof(ps_labels[0]); index++) {
        if (strcmp(label, ps_labels[index]) == 0)
            return "PS";
    }
    for (index = 0; index < sizeof(pl_labels) / sizeof(pl_labels[0]); index++) {
        if (strcmp(label, pl_labels[index]) == 0)
            return "PL";
    }
    return "OTHER";
}

static void sensor_domain(const char *label_path, const char *label,
                          char *domain, size_t size)
{
    char resolved[PATH_MAX];
    const char *value;

    value = fallback_domain(label);
    if (realpath(label_path, resolved) != NULL) {
        if (strstr(resolved, "/i2c@0/") != NULL)
            value = "PS";
        else if (strstr(resolved, "/i2c@1/") != NULL)
            value = "PL";
    }
    snprintf(domain, size, "%s", value);
}

static int compare_sensors(const void *left, const void *right)
{
    const struct sensor *a = left;
    const struct sensor *b = right;
    int result = strcmp(a->domain, b->domain);

    return result != 0 ? result : strcmp(a->label, b->label);
}

static int discover_sensors(const char *root, struct sensor *sensors,
                            size_t *sensor_count)
{
    char pattern[PATH_MAX];
    glob_t matches;
    size_t index;
    size_t count = 0;

    if (snprintf(pattern, sizeof(pattern), "%s/hwmon*/power*_input", root) >=
        (int)sizeof(pattern))
        return -1;
    memset(&matches, 0, sizeof(matches));
    if (glob(pattern, 0, NULL, &matches) != 0)
        return -1;

    for (index = 0; index < matches.gl_pathc && count < MAX_SENSORS; index++) {
        const char *path = matches.gl_pathv[index];
        const char *slash = strrchr(path, '/');
        char directory[PATH_MAX];
        char label_path[PATH_MAX];
        char *suffix;
        int fd;

        if (slash == NULL ||
            (size_t)(slash - path) >= sizeof(directory))
            continue;
        memcpy(directory, path, (size_t)(slash - path));
        directory[slash - path] = '\0';
        if (snprintf(label_path, sizeof(label_path), "%s/%s", directory,
                     slash + 1) >= (int)sizeof(label_path))
            continue;
        suffix = strstr(label_path, "_input");
        if (suffix == NULL)
            continue;
        snprintf(suffix, sizeof(label_path) - (size_t)(suffix - label_path),
                 "_label");
        if (read_text_file(label_path, sensors[count].label,
                           sizeof(sensors[count].label)) != 0) {
            if (snprintf(label_path, sizeof(label_path),
                         "%s/device/of_node/label", directory) >=
                (int)sizeof(label_path) ||
                read_text_file(label_path, sensors[count].label,
                               sizeof(sensors[count].label)) != 0)
                continue;
        }
        fd = open(path, O_RDONLY | O_CLOEXEC);
        if (fd < 0)
            continue;
        sensors[count].fd = fd;
        snprintf(sensors[count].path, sizeof(sensors[count].path), "%s", path);
        sensor_domain(label_path, sensors[count].label, sensors[count].domain,
                      sizeof(sensors[count].domain));
        count++;
    }
    globfree(&matches);
    qsort(sensors, count, sizeof(sensors[0]), compare_sensors);
    *sensor_count = count;
    return count == 0 ? -1 : 0;
}

static int read_power_uw(struct sensor *sensor, int64_t *value)
{
    char buffer[64];
    char *end;
    ssize_t count;
    long long parsed;

    if (lseek(sensor->fd, 0, SEEK_SET) < 0)
        return -1;
    count = read(sensor->fd, buffer, sizeof(buffer) - 1);
    if (count <= 0)
        return -1;
    buffer[count] = '\0';
    errno = 0;
    parsed = strtoll(buffer, &end, 10);
    if (errno != 0 || end == buffer)
        return -1;
    *value = parsed;
    return 0;
}

static int pin_cpu(int cpu)
{
    cpu_set_t set;

    if (cpu < 0)
        return 0;
    CPU_ZERO(&set);
    CPU_SET(cpu, &set);
    return sched_setaffinity(0, sizeof(set), &set);
}

static void usage(FILE *stream, const char *program)
{
    fprintf(stream,
            "usage: %s [--list] [--output FILE] [--duration-ms N | "
            "--stop-file FILE] [--interval-ms N] [--cpu N] "
            "[--hwmon-root DIR]\n",
            program);
}

int main(int argc, char **argv)
{
    const char *output_path = NULL;
    const char *stop_file = NULL;
    const char *hwmon_root = "/sys/class/hwmon";
    long duration_ms = -1;
    long interval_ms = 50;
    int cpu = -1;
    bool list_only = false;
    struct sensor sensors[MAX_SENSORS];
    size_t sensor_count = 0;
    size_t index;
    FILE *output;
    struct timespec raw_start;
    struct timespec sleep_deadline;
    uint64_t sample_index = 0;
    int status = 0;

    memset(sensors, 0, sizeof(sensors));
    for (index = 1; index < (size_t)argc; index++) {
        if (strcmp(argv[index], "--list") == 0) {
            list_only = true;
        } else if (strcmp(argv[index], "--output") == 0 && index + 1 < (size_t)argc) {
            output_path = argv[++index];
        } else if (strcmp(argv[index], "--duration-ms") == 0 &&
                   index + 1 < (size_t)argc) {
            duration_ms = strtol(argv[++index], NULL, 10);
        } else if (strcmp(argv[index], "--stop-file") == 0 &&
                   index + 1 < (size_t)argc) {
            stop_file = argv[++index];
        } else if (strcmp(argv[index], "--interval-ms") == 0 &&
                   index + 1 < (size_t)argc) {
            interval_ms = strtol(argv[++index], NULL, 10);
        } else if (strcmp(argv[index], "--cpu") == 0 && index + 1 < (size_t)argc) {
            cpu = atoi(argv[++index]);
        } else if (strcmp(argv[index], "--hwmon-root") == 0 &&
                   index + 1 < (size_t)argc) {
            hwmon_root = argv[++index];
        } else {
            usage(stderr, argv[0]);
            return 2;
        }
    }
    if (discover_sensors(hwmon_root, sensors, &sensor_count) != 0) {
        fprintf(stderr, "no labelled hwmon power sensors found under %s\n",
                hwmon_root);
        return 3;
    }
    if (list_only) {
        printf("domain,rail,path\n");
        for (index = 0; index < sensor_count; index++)
            printf("%s,%s,%s\n", sensors[index].domain, sensors[index].label,
                   sensors[index].path);
        goto close_sensors;
    }
    if (output_path == NULL || interval_ms <= 0 ||
        (duration_ms <= 0 && stop_file == NULL) ||
        (duration_ms > 0 && stop_file != NULL)) {
        usage(stderr, argv[0]);
        status = 2;
        goto close_sensors;
    }
    if (pin_cpu(cpu) != 0) {
        fprintf(stderr, "could not pin sampler to CPU %d: %s\n", cpu,
                strerror(errno));
        status = 4;
        goto close_sensors;
    }
    output = strcmp(output_path, "-") == 0 ? stdout : fopen(output_path, "w");
    if (output == NULL) {
        fprintf(stderr, "could not open %s: %s\n", output_path,
                strerror(errno));
        status = 4;
        goto close_sensors;
    }
    setvbuf(output, NULL, _IOFBF, 1024 * 1024);
    fprintf(output,
            "# nvdla-power-sampler schema=1 clock=CLOCK_MONOTONIC_RAW "
            "interval_ms=%ld sensor_count=%zu cpu=%d\n",
            interval_ms, sensor_count, cpu);
    fprintf(output, "sample_index,timestamp_ns,domain,rail,power_uw\n");

    signal(SIGINT, handle_signal);
    signal(SIGTERM, handle_signal);
    if (clock_gettime(CLOCK_MONOTONIC_RAW, &raw_start) != 0 ||
        clock_gettime(CLOCK_MONOTONIC, &sleep_deadline) != 0) {
        fprintf(stderr, "clock_gettime failed: %s\n", strerror(errno));
        status = 4;
        goto close_output;
    }
    while (!stop_requested) {
        struct timespec timestamp;

        if (sample_index > 0 && stop_file != NULL &&
            access(stop_file, F_OK) == 0)
            break;
        if (clock_gettime(CLOCK_MONOTONIC_RAW, &timestamp) != 0) {
            status = 4;
            break;
        }
        if (duration_ms > 0 &&
            timespec_ns(&timestamp) - timespec_ns(&raw_start) >=
                duration_ms * 1000000LL)
            break;
        for (index = 0; index < sensor_count; index++) {
            int64_t power_uw;

            if (read_power_uw(&sensors[index], &power_uw) != 0) {
                fprintf(stderr, "failed to read %s: %s\n",
                        sensors[index].path, strerror(errno));
                status = 5;
                stop_requested = 1;
                break;
            }
            fprintf(output, "%llu,%lld,%s,%s,%lld\n",
                    (unsigned long long)sample_index,
                    (long long)timespec_ns(&timestamp), sensors[index].domain,
                    sensors[index].label, (long long)power_uw);
        }
        sample_index++;
        sleep_deadline.tv_nsec += (interval_ms % 1000) * 1000000L;
        sleep_deadline.tv_sec += interval_ms / 1000 +
                                 sleep_deadline.tv_nsec / 1000000000L;
        sleep_deadline.tv_nsec %= 1000000000L;
        while (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME,
                               &sleep_deadline, NULL) == EINTR &&
               !stop_requested) {
        }
    }

close_output:
    if (output != stdout)
        fclose(output);
    else
        fflush(output);
close_sensors:
    for (index = 0; index < sensor_count; index++)
        close(sensors[index].fd);
    return status;
}
