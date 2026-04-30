import re


def find_boundary_markers(text, chapter_count=None):
    """Find a stable ordered chain of batch boundary markers."""
    pattern = re.compile(r'<!--\s*(\d+)\s*-->')
    occurrences = []

    for match in pattern.finditer(text):
        marker_id = int(match.group(1))
        occurrences.append((marker_id, match.start(), match.end()))

    if chapter_count is None:
        markers_map = {}
        for marker_id, start_pos, end_pos in occurrences:
            markers_map[marker_id] = (start_pos, end_pos)
        return markers_map

    by_marker_id = {
        marker_id: []
        for marker_id in range(chapter_count + 1)
    }
    for marker_id, start_pos, end_pos in occurrences:
        if marker_id in by_marker_id:
            by_marker_id[marker_id].append((start_pos, end_pos))

    if any(not positions for positions in by_marker_id.values()):
        markers_map = {}
        for marker_id, start_pos, end_pos in occurrences:
            markers_map[marker_id] = (start_pos, end_pos)
        return markers_map

    def chain_score(chain):
        cyrillic_count = 0
        content_length = 0
        for idx in range(len(chain) - 1):
            segment = text[chain[idx][1]:chain[idx + 1][0]]
            cyrillic_count += len(re.findall(r'[А-Яа-яЁё]', segment))
            content_length += len(segment.strip())
        return cyrillic_count, content_length, chain[0][0]

    beam = [[candidate] for candidate in by_marker_id[0]]
    max_beam_size = 128
    for marker_id in range(1, chapter_count + 1):
        next_beam = []
        for chain in beam:
            previous_end = chain[-1][1]
            for candidate in by_marker_id[marker_id]:
                if candidate[0] >= previous_end:
                    next_beam.append(chain + [candidate])

        if not next_beam:
            break

        next_beam.sort(key=chain_score, reverse=True)
        beam = next_beam[:max_beam_size]

    complete_chains = [
        chain
        for chain in beam
        if len(chain) == chapter_count + 1
    ]
    if not complete_chains:
        markers_map = {}
        for marker_id, start_pos, end_pos in occurrences:
            markers_map[marker_id] = (start_pos, end_pos)
        return markers_map

    best_chain = max(complete_chains, key=chain_score)
    return {
        marker_id: best_chain[marker_id]
        for marker_id in range(chapter_count + 1)
    }
