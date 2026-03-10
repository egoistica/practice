from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Iterable


TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9']+")
SENTENCE_END_RE = re.compile(r"[.!?…]\s*$")

EMBEDDING_DIM = 384
MIN_BLOCK_SECONDS = 35.0
SOFT_MIN_BLOCK_WORDS = 90
SOFT_MAX_BLOCK_WORDS = 320
DISTANCE_FLOOR = 0.18
MERGE_SIMILARITY_THRESHOLD = 0.84
MERGE_MAX_WORDS = 480

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "with",
    "в",
    "во",
    "и",
    "или",
    "как",
    "к",
    "ко",
    "на",
    "но",
    "о",
    "об",
    "от",
    "по",
    "с",
    "со",
    "у",
    "что",
    "это",
    "я",
}


@dataclass(frozen=True)
class _Segment:
    start: float
    end: float
    text: str
    words: int
    has_timestamps: bool


@dataclass
class _Block:
    start_idx: int
    end_idx: int


def segment_text(text: str, segments: list[dict]) -> list[dict]:
    normalized = _normalize_segments(text, segments)
    if not normalized:
        return []

    embeddings = _build_embeddings(normalized)
    min_words, min_seconds = _dynamic_block_limits(normalized)
    blocks = _split_by_topic_shift(normalized, embeddings, min_words, min_seconds)
    blocks = _merge_small_blocks(blocks, normalized, embeddings, min_words, min_seconds)
    blocks = _merge_similar_neighbors(blocks, normalized, embeddings)

    return [_block_to_payload(block, normalized) for block in blocks]


def _normalize_segments(text: str, raw_segments: list[dict]) -> list[_Segment]:
    if not raw_segments:
        compact = " ".join(part.strip() for part in text.splitlines() if part.strip())
        if not compact:
            return []
        words = _word_count(compact)
        return [_Segment(start=0.0, end=0.0, text=compact, words=words, has_timestamps=False)]

    normalized: list[_Segment] = []
    for item in raw_segments:
        if not isinstance(item, dict):
            continue

        raw_text = str(item.get("text", "")).strip()
        if not raw_text:
            continue

        has_raw_start = item.get("start") is not None or item.get("timecode_start") is not None
        has_raw_end = item.get("end") is not None or item.get("timecode_end") is not None
        has_timestamps = bool(has_raw_start or has_raw_end)

        start = _to_float(item.get("start", item.get("timecode_start")), 0.0)
        end = _to_float(item.get("end", item.get("timecode_end")), start)
        if end < start:
            end = start

        normalized.append(
            _Segment(
                start=start,
                end=end,
                text=raw_text,
                words=_word_count(raw_text),
                has_timestamps=has_timestamps,
            )
        )

    normalized.sort(key=lambda seg: (seg.start, seg.end))
    return normalized


def _build_embeddings(segments: list[_Segment]) -> list[list[float]]:
    token_sets: list[set[str]] = []
    token_sequences: list[list[str]] = []
    document_frequency: dict[str, int] = {}

    for segment in segments:
        tokens = _tokenize(segment.text)
        token_sequences.append(tokens)
        unique_tokens = set(tokens)
        token_sets.append(unique_tokens)
        for token in unique_tokens:
            document_frequency[token] = document_frequency.get(token, 0) + 1

    total_docs = max(len(segments), 1)
    idf = {
        token: math.log((1.0 + total_docs) / (1.0 + df)) + 1.0
        for token, df in document_frequency.items()
    }

    embeddings: list[list[float]] = []
    for idx, tokens in enumerate(token_sequences):
        embeddings.append(_text_embedding(tokens, token_sets[idx], idf))
    return embeddings


def _text_embedding(tokens: list[str], unique_tokens: set[str], idf: dict[str, float]) -> list[float]:
    if not tokens:
        return [0.0] * EMBEDDING_DIM

    tf: dict[str, int] = {}
    for token in tokens:
        tf[token] = tf.get(token, 0) + 1

    vec = [0.0] * EMBEDDING_DIM

    for token in unique_tokens:
        weight = (1.0 + math.log1p(tf.get(token, 1))) * idf.get(token, 1.0)
        idx, sign = _hash_feature(token)
        vec[idx] += sign * weight

    for first, second in zip(tokens, tokens[1:]):
        bigram = first + "::" + second
        idx, sign = _hash_feature(bigram)
        vec[idx] += sign * 0.35

    return _l2_normalize(vec)


def _split_by_topic_shift(
    segments: list[_Segment],
    embeddings: list[list[float]],
    min_words: int,
    min_seconds: float,
) -> list[_Block]:
    if len(segments) == 1:
        return [_Block(0, 0)]

    distances = [
        1.0 - _cosine_similarity(embeddings[i], embeddings[i + 1])
        for i in range(len(segments) - 1)
    ]
    threshold = _adaptive_distance_threshold(distances)

    blocks: list[_Block] = []
    block_start = 0
    block_words = segments[0].words

    for idx, distance in enumerate(distances):
        boundary_ok = _is_sentence_boundary(segments[idx].text) or distance >= (threshold + 0.07)
        duration_gate_enabled = _duration_gate_enabled(segments[block_start : idx + 1])
        block_duration = max(segments[idx].end - segments[block_start].start, 0.0)
        enough_context = block_words >= min_words and (
            block_duration >= min_seconds or not duration_gate_enabled
        )
        projected_words = block_words + segments[idx + 1].words
        force_soft_max_split = (
            projected_words > SOFT_MAX_BLOCK_WORDS
            and _is_sentence_boundary(segments[idx].text)
            and block_words >= max(8, min_words // 2)
        )

        if force_soft_max_split or (distance >= threshold and boundary_ok and enough_context):
            blocks.append(_Block(block_start, idx))
            block_start = idx + 1
            block_words = segments[block_start].words
        else:
            block_words += segments[idx + 1].words

    blocks.append(_Block(block_start, len(segments) - 1))
    return blocks


def _merge_small_blocks(
    blocks: list[_Block],
    segments: list[_Segment],
    embeddings: list[list[float]],
    min_words: int,
    min_seconds: float,
) -> list[_Block]:
    merged = list(blocks)
    changed = True
    while changed and len(merged) > 1:
        changed = False
        for idx, block in enumerate(list(merged)):
            words = _block_word_count(block, segments)
            duration = _block_duration(block, segments)
            duration_gate_enabled = _duration_gate_enabled(segments[block.start_idx : block.end_idx + 1])
            duration_ok = duration >= min_seconds or not duration_gate_enabled
            if words >= min_words and duration_ok:
                continue

            if idx == 0:
                target = 1
            elif idx == len(merged) - 1:
                target = idx - 1
            else:
                left_sim = _block_similarity(merged[idx - 1], block, embeddings)
                right_sim = _block_similarity(block, merged[idx + 1], embeddings)
                target = idx - 1 if left_sim >= right_sim else idx + 1

            merged = _merge_by_index(merged, idx, target)
            changed = True
            break
    return merged


def _merge_similar_neighbors(
    blocks: list[_Block],
    segments: list[_Segment],
    embeddings: list[list[float]],
) -> list[_Block]:
    merged = list(blocks)
    i = 0
    while i < len(merged) - 1:
        left = merged[i]
        right = merged[i + 1]
        similarity = _block_similarity(left, right, embeddings)
        combined_words = _block_word_count(left, segments) + _block_word_count(right, segments)
        max_merge_words = min(MERGE_MAX_WORDS, SOFT_MAX_BLOCK_WORDS)

        if similarity >= MERGE_SIMILARITY_THRESHOLD and combined_words <= max_merge_words:
            merged[i] = _Block(start_idx=left.start_idx, end_idx=right.end_idx)
            del merged[i + 1]
            if i > 0:
                i -= 1
            continue
        i += 1
    return merged


def _dynamic_block_limits(segments: list[_Segment]) -> tuple[int, float]:
    total_words = sum(segment.words for segment in segments)
    total_duration = max(segments[-1].end - segments[0].start, 0.0) if segments else 0.0

    min_words = min(SOFT_MIN_BLOCK_WORDS, max(14, int(total_words * 0.16)))
    min_seconds = MIN_BLOCK_SECONDS
    if total_duration and total_duration < (2.0 * MIN_BLOCK_SECONDS):
        min_seconds = max(12.0, total_duration * 0.18)

    return min_words, min_seconds


def _block_to_payload(block: _Block, segments: list[_Segment]) -> dict[str, float | str]:
    text = " ".join(segment.text for segment in segments[block.start_idx : block.end_idx + 1]).strip()
    return {
        "timecode_start": round(segments[block.start_idx].start, 3),
        "timecode_end": round(segments[block.end_idx].end, 3),
        "text": text,
    }


def _to_float(value: object, default: float) -> float:
    try:
        if value is None:
            return default
        converted = float(value)
        if not math.isfinite(converted):
            return default
        return converted
    except (TypeError, ValueError):
        return default


def _tokenize(text: str) -> list[str]:
    tokens = [match.group(0).lower() for match in TOKEN_RE.finditer(text)]
    if not tokens:
        fallback = [
            part.strip(".,!?;:()[]{}\"'`")
            for part in text.lower().split()
            if part.strip(".,!?;:()[]{}\"'`")
        ]
        tokens = fallback
    filtered = [token for token in tokens if token not in STOPWORDS]
    return filtered or tokens


def _word_count(text: str) -> int:
    regex_count = len(TOKEN_RE.findall(text))
    if regex_count:
        return regex_count
    return len([part for part in text.split() if part.strip()])


def _duration_gate_enabled(segments: list[_Segment]) -> bool:
    return all(segment.has_timestamps for segment in segments)


def _hash_feature(feature: str) -> tuple[int, float]:
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, byteorder="little", signed=False)
    idx = value % EMBEDDING_DIM
    sign = -1.0 if (value >> 1) & 1 else 1.0
    return idx, sign


def _l2_normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return vector
    return [value / norm for value in vector]


def _cosine_similarity(left: Iterable[float], right: Iterable[float]) -> float:
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for l_value, r_value in zip(left, right):
        dot += l_value * r_value
        left_norm += l_value * l_value
        right_norm += r_value * r_value

    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / math.sqrt(left_norm * right_norm)


def _adaptive_distance_threshold(distances: list[float]) -> float:
    if not distances:
        return 1.0
    sorted_distances = sorted(distances)
    p60 = _quantile(sorted_distances, 0.60)
    mean = sum(distances) / len(distances)
    variance = sum((value - mean) ** 2 for value in distances) / len(distances)
    std = math.sqrt(variance)
    raw_threshold = max(DISTANCE_FLOOR, p60, mean + 0.25 * std)
    return min(raw_threshold, 0.92)


def _quantile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    q = min(max(q, 0.0), 1.0)
    pos = (len(sorted_values) - 1) * q
    low = math.floor(pos)
    high = math.ceil(pos)
    if low == high:
        return sorted_values[low]
    fraction = pos - low
    return sorted_values[low] * (1.0 - fraction) + sorted_values[high] * fraction


def _is_sentence_boundary(text: str) -> bool:
    return bool(SENTENCE_END_RE.search(text.strip()))


def _block_word_count(block: _Block, segments: list[_Segment]) -> int:
    return sum(segment.words for segment in segments[block.start_idx : block.end_idx + 1])


def _block_duration(block: _Block, segments: list[_Segment]) -> float:
    return max(segments[block.end_idx].end - segments[block.start_idx].start, 0.0)


def _block_embedding(block: _Block, embeddings: list[list[float]]) -> list[float]:
    length = block.end_idx - block.start_idx + 1
    summed = [0.0] * EMBEDDING_DIM
    for idx in range(block.start_idx, block.end_idx + 1):
        for dim_idx, value in enumerate(embeddings[idx]):
            summed[dim_idx] += value
    return _l2_normalize([value / length for value in summed])


def _block_similarity(left: _Block, right: _Block, embeddings: list[list[float]]) -> float:
    return _cosine_similarity(_block_embedding(left, embeddings), _block_embedding(right, embeddings))


def _merge_by_index(blocks: list[_Block], source_idx: int, target_idx: int) -> list[_Block]:
    if source_idx == target_idx:
        return blocks

    source = blocks[source_idx]
    target = blocks[target_idx]
    merged_block = _Block(
        start_idx=min(source.start_idx, target.start_idx),
        end_idx=max(source.end_idx, target.end_idx),
    )

    low = min(source_idx, target_idx)
    high = max(source_idx, target_idx)
    result = blocks[:low] + [merged_block] + blocks[high + 1 :]
    return result
