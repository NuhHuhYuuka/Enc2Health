#include <openssl/rand.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <time.h>

#include "T3_ecall_aggregate.h"

#define DEFAULT_RECORDS 10000

static void die(const char *message) {
    fprintf(stderr, "[T3] %s\n", message);
    exit(EXIT_FAILURE);
}

static double now_ms(void) {
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) {
        die("clock_gettime failed");
    }
    return (double)ts.tv_sec * 1000.0 + (double)ts.tv_nsec / 1000000.0;
}

static void cleanse(void *ptr, size_t size) {
    explicit_bzero(ptr, size);
}

static void print_usage(const char *argv0) {
    fprintf(stderr, "Usage: %s --mode heap|stack|register [--records N]\n", argv0);
}

int main(int argc, char **argv) {
    const char *mode = NULL;
    size_t n_records = DEFAULT_RECORDS;

    for (int i = 1; i < argc; ++i) {
        if (strcmp(argv[i], "--mode") == 0 && i + 1 < argc) {
            mode = argv[++i];
        } else if (strcmp(argv[i], "--records") == 0 && i + 1 < argc) {
            n_records = (size_t)strtoull(argv[++i], NULL, 10);
        } else {
            print_usage(argv[0]);
            return EXIT_FAILURE;
        }
    }

    if (!mode) {
        print_usage(argv[0]);
        return EXIT_FAILURE;
    }

    uint8_t key[T3_KEY_SIZE];
    if (RAND_bytes(key, sizeof(key)) != 1) {
        die("RAND_bytes failed");
    }

    t3_encrypted_record_t *records = calloc(n_records, sizeof(*records));
    if (!records) {
        die("calloc failed for encrypted dataset");
    }

    printf("[T3] Building encrypted dataset (%zu records)\n", n_records);
    t3_build_dataset(key, records, n_records);

    double t0 = now_ms();
    double avg = 0.0;
    if (strcmp(mode, "heap") == 0) {
        avg = t3_run_heap_mode(key, records, n_records);
    } else if (strcmp(mode, "stack") == 0) {
        avg = t3_run_stack_mode(key, records, n_records);
    } else if (strcmp(mode, "register") == 0) {
        avg = t3_run_register_mode(key, records, n_records);
    } else {
        print_usage(argv[0]);
        free(records);
        return EXIT_FAILURE;
    }
    double t1 = now_ms();

    printf("[T3] mode=%s records=%zu avg=%.2f\n", mode, n_records, avg);
    printf("[T3] elapsed_ms=%.2f throughput_records_per_sec=%.2f\n",
           t1 - t0,
           (double)n_records / ((t1 - t0) / 1000.0));

    cleanse(key, sizeof(key));
    free(records);
    return EXIT_SUCCESS;
}
