#define _GNU_SOURCE

#include <immintrin.h>
#include <linux/prctl.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <time.h>

#ifndef PR_SET_SPECULATION_CTRL
#define PR_SET_SPECULATION_CTRL 53
#endif

#ifndef PR_SPEC_STORE_BYPASS
#define PR_SPEC_STORE_BYPASS 0
#endif

#ifndef PR_SPEC_DISABLE
#define PR_SPEC_DISABLE (1UL << 2)
#endif

static volatile uint64_t g_sink;

static double elapsed_ms(struct timespec start, struct timespec end) {
    return (double)(end.tv_sec - start.tv_sec) * 1000.0 + (double)(end.tv_nsec - start.tv_nsec) / 1e6;
}

static int enable_ssbd(void) {
    int ret = prctl(PR_SET_SPECULATION_CTRL, PR_SPEC_STORE_BYPASS, PR_SPEC_DISABLE, 0, 0);
    if (ret != 0) {
        perror("prctl SSBD failed");
    }
    return ret;
}

static void fill_data(uint64_t *data, size_t count) {
    for (size_t i = 0; i < count; ++i) {
        data[i] = (uint64_t)(i * 1315423911u) ^ 0x9e3779b97f4a7c15ULL;
    }
}

__attribute__((noinline))
static uint64_t run_baseline(const uint64_t *data, size_t data_count, size_t iterations) {
    uint64_t acc = 0;
    for (size_t i = 0; i < iterations; ++i) {
        uint64_t value = data[i & (data_count - 1)];
        acc ^= (value + (acc << 1));
    }
    return acc;
}

__attribute__((noinline))
static uint64_t run_mitigated(const uint64_t *data, size_t data_count, size_t iterations) {
    uint64_t acc = 0;

#pragma GCC unroll 10
    for (size_t i = 0; i < iterations; ++i) {
        uint64_t value = data[i & (data_count - 1)];
        _mm_lfence();
        acc ^= (value + (acc << 1));
    }

    return acc;
}

int main(int argc, char **argv) {
    const char *mode = (argc > 1) ? argv[1] : "baseline";
    const size_t iterations = (argc > 2) ? strtoull(argv[2], NULL, 10) : 20000000ULL;
    const size_t data_count = 1ULL << 14;

    uint64_t *data = NULL;
    if (posix_memalign((void **)&data, 64, data_count * sizeof(uint64_t)) != 0 || data == NULL) {
        fprintf(stderr, "posix_memalign failed\n");
        return 2;
    }

    fill_data(data, data_count);

    if (strcmp(mode, "mitigated") == 0) {
        enable_ssbd();
    }

    struct timespec start;
    struct timespec end;
    clock_gettime(CLOCK_MONOTONIC, &start);

    uint64_t result = 0;
    if (strcmp(mode, "mitigated") == 0) {
        result = run_mitigated(data, data_count, iterations);
    } else {
        result = run_baseline(data, data_count, iterations);
    }

    clock_gettime(CLOCK_MONOTONIC, &end);
    g_sink = result;

    const double ms = elapsed_ms(start, end);
    const double us_per_iter = (ms * 1000.0) / (double)iterations;
    const double iter_per_sec = (double)iterations / (ms / 1000.0);

    printf("mode=%s iterations=%zu elapsed_ms=%.3f iter_per_sec=%.2f us_per_iter=%.4f sink=%llu\n",
           mode,
           iterations,
           ms,
           iter_per_sec,
           us_per_iter,
           (unsigned long long)g_sink);

    free(data);
    return 0;
}
