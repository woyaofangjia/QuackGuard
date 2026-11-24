"""Public artifact hash contract v1. All relative regular files; no symlinks."""
import hashlib,json,stat
from pathlib import Path
def canonical(obj):return json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
def digest_bytes(data):return hashlib.sha256(data).hexdigest()
def digest_obj(obj):return digest_bytes(canonical(obj))
def inventory(root,exclude=()):
    root=Path(root)
    if root.is_symlink() or not root.is_dir():raise ValueError('Inventory root missing or symbolic')
    result={}
    for p in sorted(root.rglob('*')):
        rel=p.relative_to(root).as_posix()
        if p.is_symlink():raise ValueError('Symlinks cannot be sealed')
        if p.is_dir():continue
        if not stat.S_ISREG(p.stat().st_mode):raise ValueError('Special files cannot be sealed')
        if rel in exclude:continue
        if p.stat().st_size>1024**3:raise ValueError('Artifact file exceeds 1 GiB')
        result[rel]=digest_bytes(p.read_bytes())
    return result
def tree_digest(root):return digest_obj(inventory(root))
def path_digest(path):
    p=Path(path)
    if p.is_symlink():raise ValueError('Symbolic artifact')
    if p.is_dir():return tree_digest(p)
    if not p.is_file() or not stat.S_ISREG(p.stat().st_mode):raise ValueError('Nonregular artifact')
    if p.stat().st_size>1024**3:raise ValueError('Artifact file exceeds 1 GiB')
    return digest_bytes(p.read_bytes())
if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('path');a=p.parse_args();print(path_digest(a.path))
