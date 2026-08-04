/*
 * MeshCore Channel Cracker — Brute-force #hashtag channel names
 *
 * Uses OpenSSL for SHA256, HMAC-SHA256, AES-128-ECB.
 * Multi-threaded with pthreads for parallel cracking.
 *
 * Build: gcc -O3 -o channel_crack channel_crack.c -lcrypto -lpthread
 * Usage: ./channel_crack <channel_hash_hex> <mac_hex> <ciphertext_hex> [max_len] [threads]
 *
 * Example: ./channel_crack 59 dbc0 f21ea508... 8 4
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <pthread.h>
#include <time.h>
#include <signal.h>

#include <openssl/sha.h>
#include <openssl/hmac.h>
#include <openssl/evp.h>

/* --- Constants --- */
#define CHARSET "abcdefghijklmnopqrstuvwxyz0123456789"
#define CHARSET_LEN 36
#define MAX_NAME_LEN 12
#define AES_BLOCK 16

/* --- Globals --- */
static uint8_t g_target_hash;          /* 1-byte channel hash to find */
static uint8_t g_mac[2];              /* 2-byte MAC from packet */
static uint8_t g_ciphertext[256];     /* ciphertext from packet */
static int     g_ct_len;
static int     g_max_len = 8;
static int     g_num_threads = 4;
static volatile int g_found = 0;      /* set to 1 when cracked */
static char    g_result[MAX_NAME_LEN + 1];
static unsigned long long g_total_tried = 0;
static pthread_mutex_t g_mutex = PTHREAD_MUTEX_INITIALIZER;

/* Timestamp validation: 2024-01-01 to 2028-01-01 */
#define TS_MIN 1704067200U
#define TS_MAX 1830297600U

/* --- Crypto helpers --- */

static inline void sha256(const uint8_t *data, size_t len, uint8_t *out) {
    SHA256(data, len, out);
}

static inline uint8_t channel_hash_from_key(const uint8_t *key16) {
    uint8_t hash[32];
    sha256(key16, 16, hash);
    return hash[0];
}

static void channel_key_from_name(const char *name, uint8_t *key16) {
    /* Key = SHA256("#" + name)[:16] */
    char buf[256];
    int len = snprintf(buf, sizeof(buf), "#%s", name);
    uint8_t hash[32];
    sha256((uint8_t *)buf, len, hash);
    memcpy(key16, hash, 16);
}

static int verify_mac(const uint8_t *key16, const uint8_t *ct, int ct_len,
                      const uint8_t *mac) {
    /* HMAC-SHA256 with 32-byte secret (key16 + 16 zero bytes) */
    uint8_t secret[32];
    memcpy(secret, key16, 16);
    memset(secret + 16, 0, 16);

    uint8_t hmac_out[32];
    unsigned int hmac_len = 32;
    HMAC(EVP_sha256(), secret, 32, ct, ct_len, hmac_out, &hmac_len);

    return hmac_out[0] == mac[0] && hmac_out[1] == mac[1];
}

static int decrypt_and_validate(const uint8_t *key16, const uint8_t *ct, int ct_len) {
    /* AES-128-ECB decrypt */
    /* Pad ciphertext to block boundary */
    int padded_len = ct_len;
    uint8_t padded_ct[256];
    if (ct_len % AES_BLOCK != 0) {
        padded_len = ct_len + (AES_BLOCK - ct_len % AES_BLOCK);
        memcpy(padded_ct, ct, ct_len);
        memset(padded_ct + ct_len, 0, padded_len - ct_len);
    } else {
        memcpy(padded_ct, ct, ct_len);
    }

    uint8_t plaintext[256];
    int out_len = 0, final_len = 0;

    EVP_CIPHER_CTX *ctx = EVP_CIPHER_CTX_new();
    EVP_DecryptInit_ex(ctx, EVP_aes_128_ecb(), NULL, key16, NULL);
    EVP_CIPHER_CTX_set_padding(ctx, 0); /* No padding */
    EVP_DecryptUpdate(ctx, plaintext, &out_len, padded_ct, padded_len);
    EVP_DecryptFinal_ex(ctx, plaintext + out_len, &final_len);
    EVP_CIPHER_CTX_free(ctx);

    int pt_len = out_len + final_len;
    if (pt_len < 6) return 0;

    /* Check timestamp (bytes 0-3, little-endian) */
    uint32_t ts = plaintext[0] | (plaintext[1] << 8) |
                  (plaintext[2] << 16) | (plaintext[3] << 24);
    if (ts < TS_MIN || ts > TS_MAX) return 0;

    /* Check text is printable UTF-8 (bytes 5+) and has the "name: text" separator.
       The ": " requirement is the strong discriminator that rejects MAC collisions. */
    int text_start = 5;
    int text_len = 0;
    int printable = 0;
    int has_sep = 0;
    for (int i = text_start; i < pt_len; i++) {
        if (plaintext[i] == 0) break;
        text_len++;
        uint8_t c = plaintext[i];
        if ((c >= 0x20 && c <= 0x7E) || c >= 0x80) printable++;
        if (c == ':' && i + 1 < pt_len && plaintext[i + 1] == ' ') has_sep = 1;
    }
    if (text_len < 2) return 0;
    if (printable * 100 / text_len < 70) return 0;
    if (!has_sep) return 0;

    return 1; /* Looks like a valid message! */
}

static int try_name(const char *name) {
    uint8_t key[16];
    channel_key_from_name(name, key);

    /* Quick check: channel hash must match */
    if (channel_hash_from_key(key) != g_target_hash) return 0;

    /* MAC check */
    if (!verify_mac(key, g_ciphertext, g_ct_len, g_mac)) return 0;

    /* Decrypt and validate plaintext */
    if (!decrypt_and_validate(key, g_ciphertext, g_ct_len)) return 0;

    return 1;
}

/* --- Worker thread --- */

typedef struct {
    int thread_id;
    int start_len;  /* which length this thread works on */
    int end_len;
    unsigned long long tried;
} thread_arg_t;

static void enumerate(char *buf, int pos, int max_pos, unsigned long long *tried) {
    if (g_found) return;

    if (pos == max_pos) {
        buf[pos] = '\0';
        (*tried)++;
        if (try_name(buf)) {
            pthread_mutex_lock(&g_mutex);
            if (!g_found) {
                g_found = 1;
                strncpy(g_result, buf, MAX_NAME_LEN);
            }
            pthread_mutex_unlock(&g_mutex);
        }
        return;
    }

    for (int i = 0; i < CHARSET_LEN && !g_found; i++) {
        buf[pos] = CHARSET[i];
        enumerate(buf, pos + 1, max_pos, tried);
    }
}

/* Thread: each thread handles a subset of first characters for a given length */
static void *worker(void *arg) {
    thread_arg_t *ta = (thread_arg_t *)arg;
    char buf[MAX_NAME_LEN + 1];

    for (int len = ta->start_len; len <= ta->end_len && !g_found; len++) {
        if (len == 0) continue;

        /* Split work: this thread handles first chars [start..end) */
        int chars_per_thread = (CHARSET_LEN + g_num_threads - 1) / g_num_threads;
        int start_char = ta->thread_id * chars_per_thread;
        int end_char = start_char + chars_per_thread;
        if (end_char > CHARSET_LEN) end_char = CHARSET_LEN;

        for (int c = start_char; c < end_char && !g_found; c++) {
            buf[0] = CHARSET[c];
            if (len == 1) {
                buf[1] = '\0';
                ta->tried++;
                if (try_name(buf)) {
                    pthread_mutex_lock(&g_mutex);
                    if (!g_found) {
                        g_found = 1;
                        strncpy(g_result, buf, MAX_NAME_LEN);
                    }
                    pthread_mutex_unlock(&g_mutex);
                }
            } else {
                enumerate(buf, 1, len, &ta->tried);
            }
        }
    }

    pthread_mutex_lock(&g_mutex);
    g_total_tried += ta->tried;
    pthread_mutex_unlock(&g_mutex);

    return NULL;
}

/* --- Hex parsing --- */

static int hex_to_bytes(const char *hex, uint8_t *out, int max_len) {
    int len = strlen(hex);
    if (len % 2 != 0) return -1;
    int n = len / 2;
    if (n > max_len) n = max_len;
    for (int i = 0; i < n; i++) {
        unsigned int byte;
        sscanf(hex + i * 2, "%2x", &byte);
        out[i] = (uint8_t)byte;
    }
    return n;
}

/* --- Main --- */

int main(int argc, char **argv) {
    if (argc < 4) {
        fprintf(stderr, "Usage: %s <channel_hash_hex> <mac_hex> <ciphertext_hex> [max_len] [threads]\n", argv[0]);
        fprintf(stderr, "  channel_hash_hex: 1-byte hex (e.g. '59')\n");
        fprintf(stderr, "  mac_hex: 2-byte hex (e.g. 'dbc0')\n");
        fprintf(stderr, "  ciphertext_hex: encrypted data hex\n");
        fprintf(stderr, "  max_len: max channel name length (default: 8)\n");
        fprintf(stderr, "  threads: number of threads (default: 4)\n");
        return 1;
    }

    /* Parse args */
    unsigned int hash_val;
    sscanf(argv[1], "%x", &hash_val);
    g_target_hash = (uint8_t)hash_val;

    if (hex_to_bytes(argv[2], g_mac, 2) != 2) {
        fprintf(stderr, "Error: MAC must be 2 bytes (4 hex chars)\n");
        return 1;
    }

    g_ct_len = hex_to_bytes(argv[3], g_ciphertext, sizeof(g_ciphertext));
    if (g_ct_len < 1) {
        fprintf(stderr, "Error: invalid ciphertext\n");
        return 1;
    }

    if (argc >= 5) g_max_len = atoi(argv[4]);
    if (argc >= 6) g_num_threads = atoi(argv[5]);

    if (g_max_len > MAX_NAME_LEN) g_max_len = MAX_NAME_LEN;
    if (g_num_threads < 1) g_num_threads = 1;
    if (g_num_threads > 32) g_num_threads = 32;

    printf("MeshCore Channel Cracker\n");
    printf("  Target hash: 0x%02x\n", g_target_hash);
    printf("  MAC: %02x%02x\n", g_mac[0], g_mac[1]);
    printf("  Ciphertext: %d bytes\n", g_ct_len);
    printf("  Max length: %d chars\n", g_max_len);
    printf("  Threads: %d\n", g_num_threads);
    printf("  Charset: %s (%d chars)\n", CHARSET, CHARSET_LEN);
    printf("\n");

    /* Estimate total combinations */
    unsigned long long total = 0;
    unsigned long long power = 1;
    for (int i = 1; i <= g_max_len; i++) {
        power *= CHARSET_LEN;
        total += power;
    }
    printf("  Total combinations: %llu\n\n", total);

    struct timespec t_start, t_end;
    clock_gettime(CLOCK_MONOTONIC, &t_start);

    /* Launch threads — each length processed by all threads in parallel */
    for (int len = 1; len <= g_max_len && !g_found; len++) {
        struct timespec len_start;
        clock_gettime(CLOCK_MONOTONIC, &len_start);

        pthread_t threads[32];
        thread_arg_t args[32];

        for (int t = 0; t < g_num_threads; t++) {
            args[t].thread_id = t;
            args[t].start_len = len;
            args[t].end_len = len;
            args[t].tried = 0;
            pthread_create(&threads[t], NULL, worker, &args[t]);
        }
        for (int t = 0; t < g_num_threads; t++) {
            pthread_join(threads[t], NULL);
        }

        struct timespec len_end;
        clock_gettime(CLOCK_MONOTONIC, &len_end);
        double elapsed = (len_end.tv_sec - len_start.tv_sec) +
                         (len_end.tv_nsec - len_start.tv_nsec) / 1e9;

        if (g_found) {
            printf("  [%d chars] CRACKED: #%s (%.2fs)\n", len, g_result, elapsed);
        } else {
            unsigned long long len_combos = 1;
            for (int i = 0; i < len; i++) len_combos *= CHARSET_LEN;
            double rate = len_combos / elapsed;
            printf("  [%d chars] not found (%llu combos, %.2fs, %.0f/s)\n",
                   len, len_combos, elapsed, rate);
        }
    }

    clock_gettime(CLOCK_MONOTONIC, &t_end);
    double total_time = (t_end.tv_sec - t_start.tv_sec) +
                        (t_end.tv_nsec - t_start.tv_nsec) / 1e9;

    printf("\n");
    if (g_found) {
        printf("RESULT: #%s\n", g_result);
    } else {
        printf("NOT FOUND (searched %d chars in %.1fs)\n", g_max_len, total_time);
    }
    printf("Total tried: %llu (%.0f/s avg)\n", g_total_tried,
           g_total_tried / total_time);

    return g_found ? 0 : 1;
}
