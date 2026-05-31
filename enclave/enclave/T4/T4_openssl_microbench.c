/* Microbenchmark: AES-256-GCM per-record encrypt using OpenSSL EVP
 * Usage: T4_openssl_microbench <seconds> <record_size>
 * Defaults: seconds=3, record_size=40
 * Measures how many encrypt operations (records) can be done per second
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <openssl/evp.h>

static inline double timediff_sec(struct timespec a, struct timespec b) {
    return (a.tv_sec - b.tv_sec) + (a.tv_nsec - b.tv_nsec) / 1e9;
}

int main(int argc, char **argv) {
    int duration = 3;
    int record_size = 40;
    if (argc > 1) duration = atoi(argv[1]);
    if (argc > 2) record_size = atoi(argv[2]);

    unsigned char key[32];
    unsigned char iv[12];
    unsigned char *pt = malloc(record_size);
    unsigned char *ct = malloc(record_size + 16);
    unsigned char tag[16];
    if (!pt || !ct) {
        fprintf(stderr, "alloc fail\n");
        return 2;
    }
    memset(pt, 0x41, record_size);
    memset(key, 0x42, sizeof(key));

    struct timespec t0, t1;
    clock_gettime(CLOCK_MONOTONIC, &t0);

    unsigned long long iterations = 0;

    while (1) {
        /* simple per-record nonce: 12-byte with low 8 bytes = iter */
        unsigned long long v = iterations;
        memset(iv, 0, sizeof(iv));
        memcpy(iv + 4, &v, sizeof(v) < 8 ? sizeof(v) : 8);

        EVP_CIPHER_CTX *ctx = EVP_CIPHER_CTX_new();
        if (!ctx) {
            fprintf(stderr, "ctx alloc fail\n");
            return 3;
        }
        if (1 != EVP_EncryptInit_ex(ctx, EVP_aes_256_gcm(), NULL, NULL, NULL)) {
            fprintf(stderr, "init1 fail\n");
            return 4;
        }
        if (1 != EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_IVLEN, sizeof(iv), NULL)) {
            fprintf(stderr, "set ivlen fail\n");
            return 5;
        }
        if (1 != EVP_EncryptInit_ex(ctx, NULL, NULL, key, iv)) {
            fprintf(stderr, "init2 fail\n");
            return 6;
        }

        int outlen = 0;
        if (1 != EVP_EncryptUpdate(ctx, ct, &outlen, pt, record_size)) {
            fprintf(stderr, "update fail\n");
            return 7;
        }
        int tmplen = 0;
        if (1 != EVP_EncryptFinal_ex(ctx, ct + outlen, &tmplen)) {
            /* GCM may return 0 for final if all processed */
        }
        if (1 != EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_GET_TAG, sizeof(tag), tag)) {
            fprintf(stderr, "get tag fail\n");
            return 8;
        }
        EVP_CIPHER_CTX_free(ctx);

        iterations++;
        clock_gettime(CLOCK_MONOTONIC, &t1);
        if (timediff_sec(t1, t0) >= duration) break;
    }

    clock_gettime(CLOCK_MONOTONIC, &t1);
    double elapsed = timediff_sec(t1, t0);
    double rps = iterations / elapsed;

    printf("OpenSSL microbench: record_size=%d iterations=%llu elapsed=%.6fs records_per_sec=%.2f\n",
           record_size, iterations, elapsed, rps);

    free(pt);
    free(ct);
    return 0;
}
