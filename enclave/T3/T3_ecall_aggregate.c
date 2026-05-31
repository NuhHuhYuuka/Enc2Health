#include "T3_ecall_aggregate.h"

#include <immintrin.h>
#include <openssl/evp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

#define DEFAULT_VALUE_BASE 100000.0
#define DEFAULT_VALUE_STEP 13.37

static void die(const char *message) {
    fprintf(stderr, "[T3] %s\n", message);
    exit(EXIT_FAILURE);
}

static void cleanse(void *ptr, size_t size) {
    explicit_bzero(ptr, size);
}

static void derive_iv(uint64_t index, uint8_t iv[T3_IV_SIZE]) {
    memset(iv, 0, T3_IV_SIZE);
    memcpy(iv + 4, &index, sizeof(index) < 8 ? sizeof(index) : 8);
}

static void make_plaintext(double value, uint8_t plaintext[T3_RECORD_SIZE]) {
    memset(plaintext, 0, T3_RECORD_SIZE);
    memcpy(plaintext, &value, sizeof(value));
}

static void encrypt_record(const uint8_t key[T3_KEY_SIZE],
                           const uint8_t iv[T3_IV_SIZE],
                           const uint8_t plaintext[T3_RECORD_SIZE],
                           t3_encrypted_record_t *record) {
    EVP_CIPHER_CTX *ctx = EVP_CIPHER_CTX_new();
    if (!ctx) {
        die("EVP_CIPHER_CTX_new failed during encrypt");
    }

    int out_len = 0;
    int final_len = 0;

    if (EVP_EncryptInit_ex(ctx, EVP_aes_256_gcm(), NULL, NULL, NULL) != 1) {
        die("EVP_EncryptInit_ex failed");
    }
    if (EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_IVLEN, T3_IV_SIZE, NULL) != 1) {
        die("EVP_CTRL_GCM_SET_IVLEN failed");
    }
    if (EVP_EncryptInit_ex(ctx, NULL, NULL, key, iv) != 1) {
        die("EVP_EncryptInit_ex key/iv failed");
    }
    if (EVP_EncryptUpdate(ctx, record->ciphertext, &out_len, plaintext, T3_RECORD_SIZE) != 1) {
        die("EVP_EncryptUpdate failed");
    }
    if (EVP_EncryptFinal_ex(ctx, record->ciphertext + out_len, &final_len) != 1) {
        die("EVP_EncryptFinal_ex failed");
    }
    if (EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_GET_TAG, T3_TAG_SIZE, record->tag) != 1) {
        die("EVP_CTRL_GCM_GET_TAG failed");
    }

    memcpy(record->iv, iv, T3_IV_SIZE);
    EVP_CIPHER_CTX_free(ctx);
}

static void decrypt_into_buffer(const uint8_t key[T3_KEY_SIZE],
                                const t3_encrypted_record_t *record,
                                uint8_t *plaintext) {
    EVP_CIPHER_CTX *ctx = EVP_CIPHER_CTX_new();
    if (!ctx) {
        die("EVP_CIPHER_CTX_new failed during decrypt");
    }

    uint8_t iv[T3_IV_SIZE];
    uint8_t ciphertext[T3_RECORD_SIZE];
    uint8_t tag[T3_TAG_SIZE];

    memcpy(iv, record->iv, T3_IV_SIZE);
#ifdef SPECTRE_MITIGATION
    _mm_lfence();
#endif
    memcpy(ciphertext, record->ciphertext, T3_RECORD_SIZE);
#ifdef SPECTRE_MITIGATION
    _mm_lfence();
#endif
    memcpy(tag, record->tag, T3_TAG_SIZE);

#ifdef SPECTRE_MITIGATION
    _mm_lfence();
#endif

    int out_len = 0;
    int final_len = 0;

    if (EVP_DecryptInit_ex(ctx, EVP_aes_256_gcm(), NULL, NULL, NULL) != 1) {
        die("EVP_DecryptInit_ex failed");
    }
    if (EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_IVLEN, T3_IV_SIZE, NULL) != 1) {
        die("EVP_CTRL_GCM_SET_IVLEN failed");
    }
    if (EVP_DecryptInit_ex(ctx, NULL, NULL, key, iv) != 1) {
        die("EVP_DecryptInit_ex key/iv failed");
    }
    if (EVP_DecryptUpdate(ctx, plaintext, &out_len, ciphertext, T3_RECORD_SIZE) != 1) {
        die("EVP_DecryptUpdate failed");
    }
    if (EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_TAG, T3_TAG_SIZE, (void *)tag) != 1) {
        die("EVP_CTRL_GCM_SET_TAG failed");
    }
    if (EVP_DecryptFinal_ex(ctx, plaintext + out_len, &final_len) != 1) {
        die("EVP_DecryptFinal_ex failed: tag mismatch");
    }

    (void)final_len;
    EVP_CIPHER_CTX_free(ctx);
}

static double extract_double_register(const uint8_t plaintext[T3_RECORD_SIZE]) {
    __m128i block = _mm_loadu_si128((const __m128i *)plaintext);
    double value = 0.0;
    memcpy(&value, &block, sizeof(value));
    return value;
}

void t3_build_dataset(const uint8_t key[T3_KEY_SIZE],
                      t3_encrypted_record_t *records,
                      size_t n_records) {
    uint8_t plaintext[T3_RECORD_SIZE];

    for (size_t i = 0; i < n_records; ++i) {
        double value = DEFAULT_VALUE_BASE + (double)(i % 997) * DEFAULT_VALUE_STEP;
        uint8_t iv[T3_IV_SIZE];
        derive_iv((uint64_t)i, iv);
        make_plaintext(value, plaintext);
        encrypt_record(key, iv, plaintext, &records[i]);
        cleanse(plaintext, sizeof(plaintext));
    }
}

double t3_run_heap_mode(const uint8_t key[T3_KEY_SIZE],
                        const t3_encrypted_record_t *records,
                        size_t n_records) {
    double sum = 0.0;
    for (size_t i = 0; i < n_records; ++i) {
        uint8_t *plaintext = (uint8_t *)malloc(T3_RECORD_SIZE);
        if (!plaintext) {
            die("malloc failed in heap mode");
        }
        decrypt_into_buffer(key, &records[i], plaintext);
        double value = 0.0;
        memcpy(&value, plaintext, sizeof(value));
        sum += value;
        cleanse(plaintext, T3_RECORD_SIZE);
        free(plaintext);
    }
    return sum / (double)n_records;
}

double t3_run_stack_mode(const uint8_t key[T3_KEY_SIZE],
                         const t3_encrypted_record_t *records,
                         size_t n_records) {
    double sum = 0.0;
    for (size_t i = 0; i < n_records; ++i) {
        uint8_t plaintext[T3_RECORD_SIZE];
        decrypt_into_buffer(key, &records[i], plaintext);
        double value = 0.0;
        memcpy(&value, plaintext, sizeof(value));
        sum += value;
        cleanse(plaintext, sizeof(plaintext));
    }
    return sum / (double)n_records;
}

double t3_run_register_mode(const uint8_t key[T3_KEY_SIZE],
                            const t3_encrypted_record_t *records,
                            size_t n_records) {
    double sum = 0.0;
    for (size_t i = 0; i < n_records; ++i) {
        uint8_t plaintext[T3_RECORD_SIZE];
        decrypt_into_buffer(key, &records[i], plaintext);
        double value = extract_double_register(plaintext);
        sum += value;
        cleanse(plaintext, sizeof(plaintext));
    }
    return sum / (double)n_records;
}