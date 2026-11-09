from p0.p2.export_resources import cpu_peak


def test_cpu_peak_uses_elapsed_time_and_mach_timebase():
    points = [{"t": 1, "processes": {"5": [7, 0, 0]}},
              {"t": 2, "processes": {"5": [7, 2_000_000_000, 0]}}]
    assert cpu_peak(points, 1) == 200


def test_cpu_peak_does_not_count_a_reused_pid_as_cpu_time():
    points = [{"t": 1, "processes": {"5": [7, 1, 0]}},
              {"t": 2, "processes": {"5": [8, 1_000_000_000, 0]}}]
    assert cpu_peak(points, 1) == 0
