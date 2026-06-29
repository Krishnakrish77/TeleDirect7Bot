import math


def chunk_size(length):
    return 2 ** max(min(math.ceil(math.log2(length / 1024)), 10), 2) * 1024


def offset_fix(offset, chunksize):
    return offset - offset % chunksize
