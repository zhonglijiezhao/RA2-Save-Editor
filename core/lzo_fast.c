/* lzo_fast.c —— 存档编辑器用的 LZO1X 加速 DLL（解码 + 编码）
 *
 * 为什么要有它：纯 Python 解 39MB 明文要 2.59 秒、压 611 个 64KB 块要 11 秒。
 * 这里把同两个算法搬进 C，接口和 Python 版一一对应，Python 侧用 ctypes 调，
 * 拿不到 DLL / 调用出错时自动回退纯 Python（见 core/lzo_fast.py）。
 *
 * 解码器是 core/lzo1x.py（Linux kernel lzo1x_decompress_safe 的移植）的 1:1 翻译；
 * 编码器是 core/lzo_compress.py v4（贪心 + 2 槽哈希 + M2/M3/M4）的 1:1 翻译。
 *
 * 编译（VS2022 BuildTools）：
 *   cl /nologo /O2 /MT /LD lzo_fast.c /Fe:lzo_fast.dll
 */

#include <stdint.h>
#include <string.h>
#include <stdlib.h>

#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT
#endif

#define M2_MAX_OFFSET 0x800
#define MINM      3
#define MAXM      0xFFFF
#define M3_MAX_OFF 16384
#define M4_MAX_OFF 49151
#define HB        18
#define HSIZE     (1 << HB)

/* ------------------------------------------------------------------ */
/* 解码：成功返回写出字节数；失败返回负数                               */
/* ------------------------------------------------------------------ */
EXPORT int rlo_decode(const unsigned char *in, int n, unsigned char *op, int cap)
{
    int p = 0, o = 0, state = 0, bv = 0, t, nxt, m_pos, k;

    if (n < 3) return -1;
    if (n >= 5 && in[0] == 17) { bv = in[1]; p = 2; }

    if (in[p] > 17) {
        t = in[p] - 17;
        p++;
        if (t < 0 || p + t > n || o + t > cap) return -1;
        memcpy(op + o, in + p, (size_t)t);
        o += t; p += t;
        state = (t < 4) ? t : 4;
    }

    for (;;) {
        if (p >= n) return -1;
        t = in[p++];
        nxt = 0;
        m_pos = -1;

        if (t < 16) {
            if (state == 0) {
                if (t == 0) {
                    int ip_last = p;
                    while (p < n && in[p] == 0) p++;
                    if (p >= n) return -1;
                    int offset = p - ip_last;
                    t += (offset << 8) - offset + 15 + in[p];
                    p++;
                }
                t += 3;
                if (p + t > n || o + t > cap) return -1;
                memcpy(op + o, in + p, (size_t)t);
                o += t; p += t;
                state = 4;
                continue;
            } else if (state != 4) {
                nxt = t & 3;
                if (p >= n) return -1;
                m_pos = o - 1 - (t >> 2) - (in[p] << 2);
                p++;
                if (m_pos < 0) return -1;
                if (o + 2 > cap) return -1;
                op[o] = op[m_pos];
                op[o + 1] = op[m_pos + 1];
                o += 2;
                state = nxt; t = nxt;
                if (p + t > n || o + t > cap) return -1;
                memcpy(op + o, in + p, (size_t)t);
                o += t; p += t;
                continue;
            } else {
                nxt = t & 3;
                if (p >= n) return -1;
                m_pos = o - (1 + M2_MAX_OFFSET) - (t >> 2) - (in[p] << 2);
                p++;
                if (m_pos < 0) return -1;
                t = 3;
            }
        } else if (t >= 64) {
            nxt = t & 3;
            if (p >= n) return -1;
            m_pos = o - 1 - ((t >> 2) & 7) - (in[p] << 3);
            p++;
            if (m_pos < 0) return -1;
            t = (t >> 5) - 1 + 2;
        } else if (t >= 32) {
            t = (t & 31) + 2;
            if (t == 2) {
                int ip_last = p;
                while (p < n && in[p] == 0) p++;
                if (p >= n) return -1;
                int offset = p - ip_last;
                t += (offset << 8) - offset + 31 + in[p];
                p++;
            }
            m_pos = o - 1;
            if (p + 2 > n) return -1;
            nxt = in[p] | (in[p + 1] << 8);
            p += 2;
            m_pos -= nxt >> 2;
            if (m_pos < 0) return -1;
            nxt &= 3;
        } else { /* 16..31 */
            if (p + 2 > n) return -1;
            nxt = in[p] | (in[p + 1] << 8);
            if (((nxt & 0xfffc) == 0xfffc) && ((t & 0xf8) == 0x18) && bv) {
                if (p + 3 > n) return -1;
                t = (t & 7) | (in[p + 2] << 3);
                t += 0x400;
                if (o + t > cap) return -1;
                memset(op + o, 0, (size_t)t);
                o += t;
                nxt &= 3;
                p += 3;
                state = nxt; t = nxt;
                if (p + t > n || o + t > cap) return -1;
                memcpy(op + o, in + p, (size_t)t);
                o += t; p += t;
                continue;
            }
            m_pos = o;
            m_pos -= (t & 8) << 11;
            t = (t & 7) + 2;
            if (t == 2) {
                int ip_last = p;
                while (p < n && in[p] == 0) p++;
                if (p >= n) return -1;
                int offset = p - ip_last;
                t += (offset << 8) - offset + 7 + in[p];
                p++;
                if (p + 2 > n) return -1;
                nxt = in[p] | (in[p + 1] << 8);
            }
            p += 2;
            m_pos -= nxt >> 2;
            nxt &= 3;
            if (m_pos == o) {            /* 结束标记 */
                if (t != 3) return -1;
                return o;
            }
            m_pos -= 0x4000;
            if (m_pos < 0) return -1;
        }

        /* 匹配复制（允许重叠） */
        if (o + t > cap) return -1;
        for (k = 0; k < t; k++) op[o + k] = op[m_pos + k];
        o += t;

        state = nxt; t = nxt;
        if (p + t > n || o + t > cap) return -1;
        memcpy(op + o, in + p, (size_t)t);
        o += t; p += t;
    }
}

/* ------------------------------------------------------------------ */
/* 编码                                                               */
/* ------------------------------------------------------------------ */
typedef struct {
    unsigned char *dst;
    int cap;
    int o;
    int overflow;
} W;

static void w_put(W *w, const unsigned char *b, int n)
{
    if (w->o + n > w->cap) { w->overflow = 1; return; }
    memcpy(w->dst + w->o, b, (size_t)n);
    w->o += n;
}

static void w_byte(W *w, int b)
{
    if (w->o + 1 > w->cap) { w->overflow = 1; return; }
    w->dst[w->o++] = (unsigned char)b;
}

static void w_zero(W *w, int n)
{
    if (w->o + n > w->cap) { w->overflow = 1; return; }
    memset(w->dst + w->o, 0, (size_t)n);
    w->o += n;
}

static void emit_match(W *w, int length, int dist)
{
    if (length >= 3 && length <= 8 && dist <= M2_MAX_OFFSET) {
        int df = dist - 1;
        w_byte(w, ((length - 1) << 5) | ((df & 7) << 2));
        w_byte(w, df >> 3);
        return;
    }
    if (dist <= M3_MAX_OFF) {
        int df = dist - 1;
        if (length <= 33) {
            int nxt = df << 2;
            w_byte(w, 32 + (length - 2));
            w_byte(w, nxt & 0xFF);
            w_byte(w, (nxt >> 8) & 0xFF);
        } else {
            int rest = length - 33;
            int z = rest / 255, last = rest % 255;
            int nxt;
            if (last == 0) { z -= 1; last = 255; }
            nxt = df << 2;
            w_byte(w, 0x20);
            w_zero(w, z);
            w_byte(w, last);
            w_byte(w, nxt & 0xFF);
            w_byte(w, (nxt >> 8) & 0xFF);
        }
        return;
    }
    /* M4: 16384..49151 */
    {
        int hi = (dist <= 32767) ? 0 : 1;
        int df = dist - 16384 - (hi ? 0x4000 : 0);
        int nxt;
        if (length <= 9) {
            w_byte(w, 16 + (hi << 3) + (length - 2));
        } else {
            int rest = length - 9;
            int z = rest / 255, last = rest % 255;
            if (last == 0) { z -= 1; last = 255; }
            w_byte(w, 16 + (hi << 3));
            w_zero(w, z);
            w_byte(w, last);
        }
        nxt = df << 2;
        w_byte(w, nxt & 0xFF);
        w_byte(w, (nxt >> 8) & 0xFF);
    }
}

static void emit_lit(W *w, const unsigned char *data, int i, int j)
{
    int run = j - i;
    if (run < 4) { w->overflow = 1; return; }
    if (run <= 18) {
        w_byte(w, run - 3);
    } else {
        int rest = run - 18;
        int z = rest / 255, last = rest % 255;
        if (last == 0) { z -= 1; last = 255; }
        w_byte(w, 0x00);
        w_zero(w, z);
        w_byte(w, last);
    }
    w_put(w, data + i, run);
}

static int mlen(const unsigned char *d, int p, int s, int limit)
{
    int l = 0;
    while (l + 8 <= limit && memcmp(d + p + l, d + s + l, 8) == 0) l += 8;
    while (l < limit && d[p + l] == d[s + l]) l++;
    return l;
}

static int h3(const unsigned char *d, int p)
{
    uint32_t v = (uint32_t)d[p] | ((uint32_t)d[p + 1] << 8) | ((uint32_t)d[p + 2] << 16);
    return (int)((v * 0x9E3779B1u) >> (32 - HB));
}

/* 返回打包字节数；失败（溢出/参数问题）返回负数 */
EXPORT int rlo_encode(const unsigned char *data, int n, unsigned char *dst, int cap)
{
    W w;
    int *t1, *t2, *t3, *t4;
    int i;

    w.dst = dst; w.cap = cap; w.o = 0; w.overflow = 0;
    if (n == 0) { w_put(&w, (const unsigned char *)"\x11\x00\x00", 3); return w.overflow ? -1 : w.o; }

    t1 = (int *)malloc(sizeof(int) * HSIZE);
    t2 = (int *)malloc(sizeof(int) * HSIZE);
    t3 = (int *)malloc(sizeof(int) * HSIZE);
    t4 = (int *)malloc(sizeof(int) * HSIZE);
    if (!t1 || !t2 || !t3 || !t4) { free(t1); free(t2); free(t3); free(t4); return -2; }
    for (i = 0; i < HSIZE; i++) { t1[i] = -1; t2[i] = -1; t3[i] = -1; t4[i] = -1; }

    i = 0;
    while (i < n && !w.overflow) {
        int s1 = -1, s2 = -1, h = -1, best_l = 0, best_d = 0, m_found = 0;
        int length = 0, dist = 0;

        if (n - i >= 3) {
            int lim = n - i, s, q;
            int cand[4];
            h = h3(data, i);
            cand[0] = s1 = t1[h]; cand[1] = s2 = t2[h];
            cand[2] = t3[h]; cand[3] = t4[h];
            t4[h] = t3[h]; t3[h] = s2; t2[h] = s1; t1[h] = i;
            for (q = 0; q < 4; q++) {
                s = cand[q];
                if (s < 0 || s >= i) continue;
                int d = i - s;
                if (d > M4_MAX_OFF) continue;
                int l = mlen(data, i, s, (MAXM < lim ? MAXM : lim));
                if (l >= 3 && l > best_l) { best_l = l; best_d = d; }
            }
            if (best_l >= 3) { m_found = 1; length = best_l; dist = best_d; }
        }
        if (m_found && 0 < n - (i + length) && n - (i + length) < 4) m_found = 0;

        if (m_found) {
            int p;
            emit_match(&w, length, dist);
            if (length > 8) {
                int qs[2];
                qs[0] = i + length - 2; qs[1] = i + length - 1;
                for (int q = 0; q < 2; q++) {
                    p = qs[q];
                    if (p + 3 <= n) { int hh = h3(data, p); t4[hh] = t3[hh]; t3[hh] = t2[hh]; t2[hh] = t1[hh]; t1[hh] = p; }
                }
            } else {
                for (p = i + 1; p < i + length; p++) {
                    if (p + 3 <= n) { int hh = h3(data, p); t4[hh] = t3[hh]; t3[hh] = t2[hh]; t2[hh] = t1[hh]; t1[hh] = p; }
                }
            }
            i += length;
            continue;
        }

        /* 找下一个匹配，中间全当字面量 */
        {
            int j = i + 1, end = n, mj_l = 0, mj_d = 0, have_mj = 0;
            while (j < n) {
                int mj_ok = 0, b_l = 0, b_d = 0;
                if (n - j >= 3) {
                    int lim = n - j, s, q;
                    int hh = h3(data, j);
                    int a1 = t1[hh], a2 = t2[hh], a3 = t3[hh], a4 = t4[hh];
                    t4[hh] = a3; t3[hh] = a2; t2[hh] = a1; t1[hh] = j;
                    for (q = 0; q < 4; q++) {
                        s = (q == 0) ? a1 : (q == 1) ? a2 : (q == 2) ? a3 : a4;
                        if (s < 0 || s >= j) continue;
                        int d = j - s;
                        if (d > M4_MAX_OFF) continue;
                        int l = mlen(data, j, s, (MAXM < lim ? MAXM : lim));
                        if (l >= 3 && l > b_l) { b_l = l; b_d = d; }
                    }
                    if (b_l >= 3) mj_ok = 1;
                }
                if (j - i >= 4 && mj_ok && !(0 < n - (j + b_l) && n - (j + b_l) < 4)) {
                    end = j; mj_l = b_l; mj_d = b_d; have_mj = 1;
                    break;
                }
                j++;
            }
            emit_lit(&w, data, i, end);
            i = end;
            if (end >= n || !have_mj) break;
            {
                int p;
                emit_match(&w, mj_l, mj_d);
                if (mj_l > 8) {
                    int qs[2];
                    qs[0] = i + mj_l - 2; qs[1] = i + mj_l - 1;
                    for (int q = 0; q < 2; q++) {
                        p = qs[q];
                        if (p + 3 <= n) { int hh = h3(data, p); t4[hh] = t3[hh]; t3[hh] = t2[hh]; t2[hh] = t1[hh]; t1[hh] = p; }
                    }
                } else {
                    for (p = i + 1; p < i + mj_l; p++) {
                        if (p + 3 <= n) { int hh = h3(data, p); t4[hh] = t3[hh]; t3[hh] = t2[hh]; t2[hh] = t1[hh]; t1[hh] = p; }
                    }
                }
                i += mj_l;
            }
        }
    }
    w_put(&w, (const unsigned char *)"\x11\x00\x00", 3);
    free(t1); free(t2); free(t3); free(t4);
    return w.overflow ? -1 : w.o;
}
