def merge(intervals):
    ordered = sorted(([start, end] for start, end in intervals), key=lambda item: (item[0], item[1]))
    merged = []
    for start, end in ordered:
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return merged
