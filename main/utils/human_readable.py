def humanbytes(size):
    # https://stackoverflow.com/a/49361727/4723940
    if size is None:
        return ""
    power = 1024
    n = 0
    units = {0: ' ', 1: 'Ki', 2: 'Mi', 3: 'Gi', 4: 'Ti'}
    while size >= power and n < len(units) - 1:
        size /= power
        n += 1
    return f"{round(size, 2)} {units[n]}B"
