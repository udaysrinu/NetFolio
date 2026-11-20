"""Run the public tests with the standard library only: python3 tests/run.py (exit code = failures)."""
import importlib, os, sys, traceback
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))
fail = total = 0
for f in sorted(x for x in os.listdir(os.path.dirname(__file__) or '.') if x.startswith('test_') and x.endswith('.py')):
    mod = importlib.import_module(f[:-3])
    for n in sorted(k for k in dir(mod) if k.startswith('test_')):
        total += 1
        try:
            getattr(mod, n)()
        except Exception:
            fail += 1; print(f'FAIL {f}:{n}'); traceback.print_exc()
print(f'{total - fail}/{total} passed'); sys.exit(fail)
