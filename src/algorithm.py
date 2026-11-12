
def process_circle(points, state):
    # points：本圈点迹列表；state：同一数据包内保留的跨圈状态。
    if not points:
        return []

    point = points[0]
    track = {
        "Time": point["Time"],
        "Azimuth": point["Azimuth"],
        "Elevation": point["Elevation"],
        "Distance": point["Distance"],
        "BatchID": 1,  # 示例编号，实际使用时由参赛方程序分配。
        "CircleID": point["CircleID"],
        "PntID": point["PntID"],
    }
    return [track]
