/**
 * ifML.c — Inline MLP inference for ML-guided cut selection in ABC.
 *
 * Place at:  abc/src/map/if/ifML.c
 * Compile:   make -j$(nproc) OPTFLAGS="-O2 -DUSE_ML" ABC_USE_NO_READLINE=1
 *
 * Architecture: 9 -> ReLU -> 64 -> ReLU -> 32 -> raw score
 *               (no sigmoid — raw logit preserves ranking order)
 *
 * ROOT CAUSE OF ALL-ZEROS QoR (now fixed):
 *
 *  [BUG-CRITICAL] If_CutCopySafe was doing a PARTIAL copy that missed
 *    iCutFunc — the index into the cut function library. This is how
 *    If_ManDeriveMapping knows which gate to instantiate. With iCutFunc
 *    wrong, ABC silently falls back to its own choice for every node.
 *    FIX: Use If_CutCopy(p, dst, src) which does
 *         memcpy(dst, src, p->nCutBytes)  — a full copy of every byte.
 *
 *  [BUG-2] This ABC has an MLScore field in If_Cut_t (confirmed from
 *    struct inspection). Writing to pCut->MLScore for every cut lets
 *    ABC's existing code (if wired for it) use these scores directly,
 *    making the cut copy a belt-and-suspenders fallback.
 *
 *  [BUG-3] No diagnostic output — impossible to tell if function fires.
 *    FIX: fprintf(stderr, "[ML] ...") in If_ManMLPostProcess.
 *         Look for "[ML]" lines in your terminal when running abc_ml.
 */

#include "if.h"
#include "model_weights.h"
#include <math.h>
#include <string.h>
#include <stdlib.h>
#include <stdio.h>

/* Minimum ML score advantage to override ABC's choice.
 * Raw logit scale (no sigmoid), so 0.1 means ~2.5% sigmoid difference.
 * Set to 0.0 to always override regardless of margin. */
#define ML_MIN_MARGIN  0.1f

/* ── Activation functions ─────────────────────────────────────────────────── */
static float ml_relu(float x) { return x > 0.0f ? x : 0.0f; }

static void ml_linear(const float *x, int in_dim,
                       const float *W, const float *b,
                       float *out,     int out_dim)
{
    int i, j;
    for (i = 0; i < out_dim; i++) {
        float s = b[i];
        for (j = 0; j < in_dim; j++)
            s += W[i * in_dim + j] * x[j];
        out[i] = s;
    }
}

/* ── MLP forward pass: returns raw logit score (higher = better cut) ──────── */
static float If_MLScoreCut(const If_Cut_t *pCut, const If_Obj_t *pObj)
{
    float input[ML_INPUT_DIM], h1[ML_HIDDEN1], h2[ML_HIDDEN2], out1[1];
    int   i;
    const float eps = 1e-6f;

    const int   n_leaves   = (int)pCut->nLeaves;
    const int   node_level = (int)pObj->Level;
    const float area_flow  = (float)pCut->Area;
    const float cut_delay  = (float)pCut->Delay;

    /* Fanout: nRefs is the reference count set during mapping.
     * This is the correct fanout signal to use post-mapping. */
    const int   nrefs  = (int)pObj->nRefs;
    const float fanout = (float)(nrefs < 1 ? 1 : nrefs);

    /* Required time / slack */
    float req_time = (float)pObj->Required;
    if (req_time > 1e7f || req_time < -1e7f)
        req_time = cut_delay + 100.0f;   /* clamp ABC_INFINITY */
    float slack = req_time - cut_delay;
    if (slack >  1e7f) slack =  100.0f;
    if (slack < -1e7f) slack = -100.0f;
    const float req_abs = fabsf(req_time) + eps;

    /* mffc_size: stored in pCut->Cost by the training ABC patch.
     * If Cost == 0 (unpatched), fall back to rounded area_flow proxy. */
    int mffc_int = (int)pCut->Cost;
    if (mffc_int < 1)
        mffc_int = (int)(area_flow + 0.5f);
    if (mffc_int < n_leaves) mffc_int = n_leaves;
    if (mffc_int > 1000)     mffc_int = 1000;
    const float mffc_f = (float)mffc_int;

    /* Feature vector — ORDER must match FEATURE_NAMES in 05a_preprocess_mac.py:
       [0] n_leaves         [1] node_level        [2] node_fanout
       [3] is_critical      [4] slack_ratio        [5] fanout_adj_area
       [6] area_per_leaf    [7] mffc_size          [8] mffc_per_leaf    */
    i = 0;
    input[i++] = (float)n_leaves;
    input[i++] = (float)node_level;
    input[i++] = fanout;
    input[i++] = (slack <= 0.0f) ? 1.0f : 0.0f;
    input[i++] = slack / req_abs;
    input[i++] = area_flow / (sqrtf(fanout) + eps);
    input[i++] = area_flow / ((float)n_leaves + eps);
    input[i++] = mffc_f;
    input[i++] = mffc_f / ((float)n_leaves + eps);

    /* StandardScaler normalise */
    for (i = 0; i < ML_INPUT_DIM; i++)
        input[i] = (input[i] - scaler_mean[i]) / (scaler_scale[i] + 1e-8f);

    /* Forward: 9 → 64 → 32 → 1  (no final activation — raw logit) */
    ml_linear(input, ML_INPUT_DIM, L0_weight, L0_bias, h1, ML_HIDDEN1);
    for (i = 0; i < ML_HIDDEN1; i++) h1[i] = ml_relu(h1[i]);
    ml_linear(h1, ML_HIDDEN1,     L1_weight, L1_bias, h2, ML_HIDDEN2);
    for (i = 0; i < ML_HIDDEN2; i++) h2[i] = ml_relu(h2[i]);
    ml_linear(h2, ML_HIDDEN2,     L2_weight, L2_bias, out1, 1);
    return out1[0];
}

/* ── Static counters for diagnostic output ────────────────────────────────── */
static int s_mlNodesTotal    = 0;
static int s_mlNodesOverride = 0;

/* ── Per-node override ────────────────────────────────────────────────────── */
/*
 * Called from If_ObjPerformMappingAnd on Mode==2 (final area round),
 * BEFORE If_ManDerefNodeCutSet frees the cut set.
 *
 * Two-step approach:
 *   Step A — Score every cut and write pCut->MLScore.
 *   Step B — Copy ML-best cut into If_ObjCutBest(pObj) using
 *            If_CutCopy (full nCutBytes memcpy including iCutFunc).
 */
int If_ObjOverrideCutWithML(If_Man_t *p, If_Obj_t *pObj)
{
    If_Cut_t *pCut, *pCutBestML = NULL;
    float     bestScore = -1e30f, abcScore = -1e30f;
    int       k, nL;

    if (If_ObjIsCi(pObj) || If_ObjIsConst1(pObj)) return 0;
    if (!pObj->pCutSet || pObj->pCutSet->nCuts <= 0) return 0;

    s_mlNodesTotal++;

    If_Cut_t *pBest    = If_ObjCutBest(pObj);
    float     abcDelay = (float)pBest->Delay;
    float     slack    = (float)pObj->Required - abcDelay;

    /* ── Step A: score every cut, write MLScore ─────────────────────────── */
    k = 0;
    If_ObjForEachCut(pObj, pCut, k)
    {
        if (pCut->nLeaves == 0) continue;

        /* Timing guard: on critical path, don't consider delay-violating cuts.
         * 0.5 tolerance handles floating-point rounding between rounds. */
        if (slack < 0.5f && (float)pCut->Delay > abcDelay + 0.5f)
        {
            pCut->MLScore = -1e30f;
            continue;
        }

        float score = If_MLScoreCut(pCut, pObj);
        pCut->MLScore = score;

        if (score > bestScore) { bestScore = score; pCutBestML = pCut; }
        if (pCut == pBest)     { abcScore  = score; }
    }

    if (!pCutBestML)                          return 0;
    if (pCutBestML == pBest)                  return 0;  /* already best */
    if (bestScore - abcScore < ML_MIN_MARGIN) return 0;

    nL = (int)pCutBestML->nLeaves;
    if (nL < 1 || nL > 6) return 0;   /* K=6 hard limit */

    /* ── Step B: full cut copy using ABC's own If_CutCopy ──────────────── */
    If_CutCopy(p, If_ObjCutBest(pObj), pCutBestML);
    s_mlNodesOverride++;
    return 1;   /* signal: we overrode this node */
}

/* ── Summary printer: call once after the final mapping round ──────────────── */
void If_ManMLPostProcess(If_Man_t *p)
{
    (void)p;
    fprintf(stderr,
            "[ML] Override summary: %d/%d nodes overridden (%.1f%%)\n",
            s_mlNodesOverride, s_mlNodesTotal,
            s_mlNodesTotal > 0 ? 100.0 * s_mlNodesOverride / s_mlNodesTotal : 0.0);
    fflush(stderr);
    /* Reset for next invocation */
    s_mlNodesTotal    = 0;
    s_mlNodesOverride = 0;
}

void If_ManMLScoreAllCuts(If_Man_t *p) { (void)p; }