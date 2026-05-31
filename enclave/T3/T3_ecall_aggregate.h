#ifndef T3_ECALL_AGGREGATE_H
#define T3_ECALL_AGGREGATE_H

#include <stddef.h>
#include <stdint.h>

#define T3_KEY_SIZE 32
#define T3_IV_SIZE 12
#define T3_TAG_SIZE 16
#define T3_RECORD_SIZE 16

typedef struct {
    uint8_t iv[T3_IV_SIZE];
    uint8_t ciphertext[T3_RECORD_SIZE];
    uint8_t tag[T3_TAG_SIZE];
} t3_encrypted_record_t;

void t3_build_dataset(const uint8_t key[T3_KEY_SIZE],
                      t3_encrypted_record_t *records,
                      size_t n_records);

double t3_run_heap_mode(const uint8_t key[T3_KEY_SIZE],
                        const t3_encrypted_record_t *records,
                        size_t n_records);

double t3_run_stack_mode(const uint8_t key[T3_KEY_SIZE],
                         const t3_encrypted_record_t *records,
                         size_t n_records);

double t3_run_register_mode(const uint8_t key[T3_KEY_SIZE],
                            const t3_encrypted_record_t *records,
                            size_t n_records);

#endif