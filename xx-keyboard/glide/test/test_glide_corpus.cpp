/* Corpus replay through the public C API.
 *
 * Port of ref/xx-keyboard/tools/glide-replay: reads the vendored
 * corpus.jsonl (keyboard geometry + sampled path per line) and words.txt
 * (intended word per line), decodes each gesture via xxglide_decode, and
 * reports top-1 / top-4 hit rates. Unlike the replay harness, which drives
 * the decoder classes directly, this goes through xxglide_set_layout with
 * letters only (per the C API contract: non-letter keys are not passed),
 * so the rate also guards the contract path the UI lane will use.
 *
 * Paths come from -D defines set by meson.build. Fails when top-1 or top-4
 * drops below the floor documented in meson.build.
 */
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <fstream>
#include <map>
#include <string>
#include <vector>

#include "../xxglide.h"

namespace {

struct Key {
    int code, x, y, w, h;
};

struct Gesture {
    int kw = 0, kh = 0, pitch = 0;
    std::string layout, word;
    std::vector<Key> keys;
    std::vector<float> xs, ys;
    std::vector<int> times;
};

const char *skipTo(const char *p, char c) {
    while (*p && *p != c) {
        ++p;
    }
    return p;
}

std::vector<std::vector<int>> readNumberLists(const char *&p) {
    std::vector<std::vector<int>> out;
    p = skipTo(p, '[');
    if (!*p) {
        return out;
    }
    ++p;
    while (*p) {
        while (*p == ',' || *p == ' ') {
            ++p;
        }
        if (*p == ']') {
            ++p;
            break;
        }
        if (*p != '[') {
            break;
        }
        ++p;
        std::vector<int> row;
        while (*p && *p != ']') {
            while (*p == ',' || *p == ' ') {
                ++p;
            }
            if (*p == ']') {
                break;
            }
            row.push_back(static_cast<int>(strtol(p, const_cast<char **>(&p), 10)));
        }
        if (*p == ']') {
            ++p;
        }
        out.push_back(row);
    }
    return out;
}

int readIntField(const std::string &line, const char *key) {
    const size_t at = line.find(key);
    if (at == std::string::npos) {
        return 0;
    }
    const char *p = line.c_str() + at + strlen(key);
    p = skipTo(p, ':');
    if (*p) {
        ++p;
    }
    return static_cast<int>(strtol(p, nullptr, 10));
}

std::string readStringField(const std::string &line, const char *key) {
    const size_t at = line.find(key);
    if (at == std::string::npos) {
        return std::string();
    }
    const char *p = line.c_str() + at + strlen(key);
    p = skipTo(p, ':');
    if (*p) {
        ++p;
    }
    while (*p == ' ') {
        ++p;
    }
    if (*p != '"') {
        return std::string();
    }
    ++p;
    const char *end = strchr(p, '"');
    if (!end) {
        return std::string();
    }
    return std::string(p, end);
}

bool parseGesture(const std::string &line, Gesture *g) {
    g->kw = readIntField(line, "\"kw\"");
    g->kh = readIntField(line, "\"kh\"");
    g->pitch = readIntField(line, "\"pitch\"");
    g->layout = readStringField(line, "\"layout\"");
    if (g->layout.empty()) {
        g->layout = "tester";
    }
    g->word = readStringField(line, "\"word\"");
    const size_t keysAt = line.find("\"keys\"");
    if (keysAt == std::string::npos) {
        return false;
    }
    const char *p = line.c_str() + keysAt;
    for (const auto &row : readNumberLists(p)) {
        if (row.size() >= 5) {
            g->keys.push_back({row[0], row[1], row[2], row[3], row[4]});
        }
    }
    const size_t pathAt = line.find("\"path\"");
    if (pathAt == std::string::npos) {
        return false;
    }
    p = line.c_str() + pathAt;
    for (const auto &row : readNumberLists(p)) {
        if (row.size() >= 3) {
            g->xs.push_back(static_cast<float>(row[0]));
            g->ys.push_back(static_cast<float>(row[1]));
            g->times.push_back(row[2]);
        }
    }
    return !g->keys.empty() && !g->xs.empty();
}

int modalOf(std::vector<int> v) {
    std::map<int, int> counts;
    for (int x : v) {
        ++counts[x];
    }
    int best = 0, bestCount = 0;
    for (const auto &kv : counts) {
        if (kv.second > bestCount) {
            bestCount = kv.second;
            best = kv.first;
        }
    }
    return best;
}

std::string lower(std::string s) {
    for (char &c : s) {
        if (c >= 'A' && c <= 'Z') {
            c += 32;
        }
    }
    return s;
}

} // namespace

int main() {
    std::vector<std::string> targets;
    {
        std::ifstream f(XXGLIDE_TEST_WORDS);
        std::string w;
        while (std::getline(f, w)) {
            while (!w.empty() && (w.back() == '\r' || w.back() == ' ')) {
                w.pop_back();
            }
            if (!w.empty()) {
                targets.push_back(w);
            }
        }
    }
    std::vector<Gesture> gestures;
    {
        std::ifstream f(XXGLIDE_TEST_CORPUS);
        std::string line;
        while (std::getline(f, line)) {
            if (line.size() < 8) {
                continue;
            }
            Gesture g;
            if (parseGesture(line, &g)) {
                gestures.push_back(g);
            }
        }
    }
    if (gestures.empty()) {
        printf("no gestures parsed\n");
        return 1;
    }
    XxGlide *glide = xxglide_new(XXGLIDE_TEST_DICT);
    if (!glide) {
        printf("cannot open dictionary\n");
        return 1;
    }

    const int kTopN = 4;
    int top1 = 0, topN = 0, scored = 0;
    std::map<std::string, int> layoutTop1, layoutTopN, layoutScored;
    long long decodeUs = 0;
    int nDecoded = 0;
    for (size_t i = 0; i < gestures.size(); ++i) {
        const Gesture &g = gestures[i];
        const std::string target =
                !g.word.empty() ? g.word : (i < targets.size() ? targets[i] : std::string());

        /* Letters only, per the C API contract; corpus codepoints are
         * uppercase, the API takes lowercase. */
        std::vector<XxGlideKey> keys;
        std::vector<int> widths, heights, xSteps, ySteps;
        for (const Key &k : g.keys) {
            int code = k.code;
            if (code >= 'A' && code <= 'Z') {
                code += 32;
            }
            if (code < 'a' || code > 'z') {
                continue;
            }
            keys.push_back({code, (float)k.x, (float)k.y, (float)k.w, (float)k.h});
            widths.push_back(k.w);
            heights.push_back(k.h);
        }
        {
            std::map<int, std::vector<int>> rows;
            for (const Key &k : g.keys) {
                rows[k.y].push_back(k.x);
            }
            int prevY = -1;
            for (auto &row : rows) {
                std::sort(row.second.begin(), row.second.end());
                for (size_t j = 1; j < row.second.size(); ++j) {
                    xSteps.push_back(row.second[j] - row.second[j - 1]);
                }
                if (prevY >= 0) {
                    ySteps.push_back(row.first - prevY);
                }
                prevY = row.first;
            }
        }
        const int modalW = modalOf(widths), modalH = modalOf(heights);
        const int stepW = xSteps.empty() ? modalW : modalOf(xSteps);
        /* Synthetic lines carry the exact widget pitch; tester lines
         * fall back to the pitch-vs-key-width heuristic. */
        const int pitch = g.pitch > 0 ? g.pitch
                : (stepW >= modalW && stepW < modalW * 3 / 2) ? stepW : modalW;
        (void)modalH;

        char **out = nullptr;
        int n = 0;
        if (xxglide_set_layout(glide, keys.data(), (int)keys.size(), g.kw, g.kh, pitch)) {
            const clock_t t0 = clock();
            n = xxglide_decode(glide, g.xs.data(), g.ys.data(), g.times.data(),
                    (int)g.xs.size(), nullptr, &out, 8);
            decodeUs += (clock() - t0) * 1000000LL / CLOCKS_PER_SEC;
            ++nDecoded;
        }
        std::vector<std::string> words;
        for (int k = 0; k < n && out; ++k) {
            words.push_back(out[k]);
        }

        if (!target.empty()) {
            ++scored;
            ++layoutScored[g.layout];
            const std::string want = lower(target);
            if (!words.empty() && lower(words[0]) == want) {
                ++top1;
                ++layoutTop1[g.layout];
            }
            for (int k = 0; k < kTopN && k < (int)words.size(); ++k) {
                if (lower(words[k]) == want) {
                    ++topN;
                    ++layoutTopN[g.layout];
                    break;
                }
            }
        }
        printf("%2zu %-8s %-11s pts=%3d ->", i + 1, g.layout.c_str(), target.c_str(),
                (int)g.xs.size());
        for (int k = 0; k < kTopN && k < (int)words.size(); ++k) {
            printf(" %s", words[k].c_str());
        }
        printf("\n");
        xxglide_free_words(out, n > 0 ? n : 0);
    }
    xxglide_free(glide);

    for (const auto &kv : layoutScored) {
        printf("layout %-8s top1 %d/%d  top%d %d/%d\n", kv.first.c_str(),
                layoutTop1[kv.first], kv.second, kTopN, layoutTopN[kv.first], kv.second);
    }
    printf("top1 %d/%d  top%d %d/%d\n", top1, scored, kTopN, topN, scored);
    if (nDecoded > 0) {
        printf("decode %.1f ms/gesture avg (%d gestures)\n",
                decodeUs / 1000.0 / nDecoded, nDecoded);
    }
    if (top1 < XXGLIDE_CORPUS_TOP1_FLOOR || topN < XXGLIDE_CORPUS_TOP4_FLOOR) {
        printf("below floor top1>=%d top4>=%d\n",
                XXGLIDE_CORPUS_TOP1_FLOOR, XXGLIDE_CORPUS_TOP4_FLOOR);
        return 1;
    }
    return 0;
}
