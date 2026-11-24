"""Annotation value equivalence. Business-specific model-ID rules are separate."""
import unicodedata
def annotation_values_equal(a,b):
    if type(a) is not type(b):return False
    if isinstance(a,str):return unicodedata.normalize('NFKC',a).strip()==unicodedata.normalize('NFKC',b).strip()
    if isinstance(a,list):return len(a)==len(b) and all(annotation_values_equal(x,y) for x,y in zip(a,b))
    if isinstance(a,dict):return a.keys()==b.keys() and all(annotation_values_equal(a[k],b[k]) for k in a)
    return a==b
