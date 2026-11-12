"""Binding generation is serialized and swapped in atomically: two
workers used to rmtree the cache under each other's scanner (Librem,
pywayland 0.4.19: "No such file ... wayland.py")."""
import threading
from pathlib import Path


def test_concurrent_generation_runs_the_scanner_once(tmp_path, monkeypatch):
    import wayland_bindings as wb
    runs = []
    gate = threading.Event()

    def fake_scanner(package_dir, base, targets):
        runs.append(package_dir)
        gate.wait(0.2)
        (Path(package_dir) / '__init__.py').write_text('')

    monkeypatch.setattr(wb, '_run_scanner', fake_scanner)
    base = tmp_path / 'wayland.xml'
    base.write_text('<protocol name="wayland"/>')
    other = tmp_path / 'other.xml'
    other.write_text('<protocol name="other"/>')
    target = tmp_path / 'cache' / 'xx_wm_wayland_proto'
    threads = [threading.Thread(target=wb.generate_protocol_bindings,
                                args=(target, [base, other])) for _ in range(4)]
    for th in threads:
        th.start()
    gate.set()
    for th in threads:
        th.join()
    assert len(runs) == 1
    assert (target / '__init__.py').exists()
    assert (target / wb._COMPLETE_MARKER).exists()
    assert not [p for p in target.parent.iterdir() if '.tmp-' in p.name]


def test_failed_scanner_leaves_no_half_written_cache(tmp_path, monkeypatch):
    import pytest
    import wayland_bindings as wb

    def broken(package_dir, base, targets):
        (Path(package_dir) / 'partial.py').write_text('')
        raise RuntimeError('scanner failed')

    monkeypatch.setattr(wb, '_run_scanner', broken)
    base = tmp_path / 'wayland.xml'
    base.write_text('<protocol name="wayland"/>')
    other = tmp_path / 'o.xml'
    other.write_text('<protocol name="o"/>')
    target = tmp_path / 'cache' / 'pkg'
    with pytest.raises(RuntimeError):
        wb.generate_protocol_bindings(target, [base, other])
    assert not target.exists()
    assert not [p for p in target.parent.iterdir() if '.tmp-' in p.name]


def test_concurrent_loads_of_different_sets_all_succeed(tmp_path):
    """Every set imports under one package name; the shell's workers load
    theirs at the same time at startup. Unserialized, one swapped the
    package out under another's import and that set came back None (the
    FLX1s lost output power that way)."""
    import sys
    import wayland_bindings as wb
    sets = []
    for i in range(5):
        pkg = tmp_path / f'set{i}' / 'xx_wm_race_proto'
        (pkg / 'proto').mkdir(parents=True)
        (pkg / '__init__.py').write_text('')
        (pkg / 'proto' / '__init__.py').write_text('')
        (pkg / 'proto' / 'mod.py').write_text(
            f'import time\ntime.sleep(0.01)\nclass Set{i}:\n    pass\n')
        sets.append((pkg, (f'Set{i}',)))
    results = {}

    def load(index):
        pkg, names = sets[index]
        results[index] = wb.load_classes_from(pkg, names)

    try:
        for _round in range(10):
            wb._drop_cached_modules('xx_wm_race_proto')
            results.clear()
            threads = [threading.Thread(target=load, args=(i,))
                       for i in range(len(sets))]
            for th in threads:
                th.start()
            for th in threads:
                th.join()
            assert all(results[i] is not None for i in range(len(sets))), results
    finally:
        wb._drop_cached_modules('xx_wm_race_proto')
        sys.modules.pop('xx_wm_race_proto', None)
