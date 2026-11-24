"""Public protocol 1.3.1 validator. No reference answers or organizer secrets."""
import json,math,re,stat
from pathlib import Path
LABELS=('pass','reject','ambiguous')
APPLICABILITY=('applicable','not_applicable','unknown')
MAX_FILE_BYTES=64*1024*1024
MAX_LINE_BYTES=512*1024
MAX_RECORDS=100000

class ContractError(ValueError):pass

def nonempty(x,maxlen=16384):return isinstance(x,str) and bool(x.strip()) and len(x)<=maxlen

def number(x):return type(x) in (int,float) and math.isfinite(x) and x>=0

def integer(x,minimum=1):return type(x) is int and x>=minimum

def strict_loads(text):
    def pairs(items):
        out={}
        for k,v in items:
            if k in out:raise ContractError('Duplicate JSON key: '+k)
            out[k]=v
        return out
    def invalid(v):raise ContractError('Non-finite JSON constant: '+v)
    try:return json.loads(text,object_pairs_hook=pairs,parse_constant=invalid)
    except (json.JSONDecodeError,RecursionError) as e:raise ContractError('Malformed JSON') from e

def regular_bytes(path,limit=MAX_FILE_BYTES):
    p=Path(path)
    if p.is_symlink():raise ContractError('Symlink is not an accepted data file')
    st=p.stat()
    if not stat.S_ISREG(st.st_mode) or st.st_size>limit:raise ContractError('Invalid file type or size')
    with p.open('rb') as f:data=f.read(limit+1)
    if len(data)>limit:raise ContractError('File exceeded limit while reading')
    return data

def read_rows(path,missing_ok=True):
    p=Path(path)
    if not p.exists():
        if p.is_symlink():raise ContractError('Broken symlink')
        if missing_ok:return []
        raise ContractError('Required file missing')
    try:text=regular_bytes(p).decode('utf-8')
    except UnicodeDecodeError as e:raise ContractError('JSONL must be UTF-8') from e
    rows=[]
    for i,line in enumerate(text.split('\n'),1):
        line=line.rstrip('\r')
        if not line.strip():continue
        if len(line.encode())>MAX_LINE_BYTES:raise ContractError('Oversized JSONL line')
        try:rows.append(strict_loads(line))
        except ContractError as e:raise ContractError(f'{p.name}:{i}: {e}') from e
        if len(rows)>MAX_RECORDS:raise ContractError('Too many records')
    return rows

def bbox_ok(b):
    return isinstance(b,list) and len(b)==4 and all(number(x) and x<=1 for x in b) and b[0]<b[2] and b[1]<b[3]

def iou(a,b):
    if not bbox_ok(a) or not bbox_ok(b):return 0.
    inter=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
    union=(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-inter
    return inter/union if union else 0.

def safe_relative(x):
    if not nonempty(x,4096) or '\\' in x or '\x00' in x:return False
    p=Path(x)
    return not p.is_absolute() and all(part not in ('.','..','') for part in x.split('/')) and ':' not in x

def input_errors(task):
    if not isinstance(task,dict):return ['input_not_object']
    keys={'audit_unit_id','sample_id','rule_id','rule_version','review_date','metadata','materials'}
    if set(task)!=keys:return ['input_fields']
    if any(not nonempty(task[k],256) for k in keys-{'metadata','materials'}):return ['input_identifier']
    if not isinstance(task['metadata'],dict) or not isinstance(task['materials'],list):return ['input_structure']
    mids=set()
    for m in task['materials']:
        if not isinstance(m,dict) or set(m)!={'material_id','role','relative_path','media_type','page_index'}:return ['material_fields']
        if not nonempty(m['material_id'],128) or m['material_id'] in mids:return ['material_id']
        mids.add(m['material_id'])
        if not nonempty(m['role'],128) or not safe_relative(m['relative_path']):return ['material_path_or_role']
        if m['media_type'] not in ['image/png','image/jpeg','application/pdf'] or not integer(m['page_index']):return ['material_type_or_page']
    return []

def visual_evidence_required(task,applicability):
    """Public, deterministic minimum; it does not assert that evidence content is true."""
    if applicability in ['not_applicable','unknown'] or not task['materials']:return False
    rid=task['rule_id'];m=task['metadata'];ctx=m.get('material_context',{})
    if not isinstance(ctx,dict):ctx={}
    def contexts():return [ctx.get(x['material_id'],{}) for x in task['materials']]
    if rid=='R002' and isinstance(m.get('required_faces'),list):
        faces={x.get('face_id') for x in contexts() if isinstance(x,dict) and isinstance(x.get('face_id'),str)}
        if any(face not in faces for face in m['required_faces']):return False
    if rid=='R006' and contexts() and all(isinstance(x,dict) and x.get('source_type') in ['design','render','unknown'] for x in contexts()):return False
    if rid=='R019' and not any(isinstance(x,dict) and x.get('face_id')=='front' for x in contexts()):return False
    if rid=='R022' and m.get('package_longest_edge_mm') is None:return False
    if rid in ['R024','R030'] and contexts() and all(isinstance(x,dict) and x.get('is_standard_front_view') is False for x in contexts()):return False
    if rid=='R059' and m.get('declared_color_name') in ['蓝色','蓝盒','蓝色练习盒']:return False
    return True

def prediction_errors(row,task,expected_artifact_version=None):
    if not isinstance(row,dict):return ['prediction_not_object']
    required={'audit_unit_id','rule_id','rule_version','execution_status','label','applicability','reason_code','reason','evidence','artifact_version','trace_id','latency_ms','cost_units'}
    if not required<=set(row) or set(row)-required-{'error_code','confidence'}:return ['prediction_fields']
    if any(not nonempty(row[k],256) for k in ['audit_unit_id','rule_id','rule_version','artifact_version','trace_id']):return ['prediction_identifier']
    if any(row[k]!=task[k] for k in ['audit_unit_id','rule_id','rule_version']):return ['wrong_unit_rule_or_version']
    if expected_artifact_version is not None and row['artifact_version']!=expected_artifact_version:return ['artifact_version_not_executed']
    if not nonempty(row['reason']) or not nonempty(row['reason_code'],256):return ['blank_reason']
    if not number(row['latency_ms']) or (row['cost_units'] is not None and not number(row['cost_units'])):return ['invalid_resource_value']
    if 'confidence' in row and (not number(row['confidence']) or row['confidence']>1):return ['invalid_confidence']
    if 'error_code' in row and not nonempty(row['error_code'],256):return ['invalid_error_code']
    if row['execution_status']=='completed':
        if not isinstance(row['label'],str) or row['label'] not in LABELS:return ['invalid_label']
        if not isinstance(row['applicability'],str) or row['applicability'] not in APPLICABILITY:return ['invalid_applicability']
        if row['applicability']=='not_applicable' and (row['label']!='pass' or row['reason_code']!='rule_not_applicable'):return ['inconsistent_not_applicable']
        if row['applicability']=='unknown' and row['label']!='ambiguous':return ['inconsistent_unknown']
    elif row['execution_status']=='error':
        if row['label'] is not None or not nonempty(row.get('error_code'),256):return ['invalid_execution_error']
        if row['applicability'] is not None and (not isinstance(row['applicability'],str) or row['applicability'] not in APPLICABILITY):return ['invalid_error_applicability']
    else:return ['invalid_execution_status']
    if not isinstance(row['evidence'],list) or len(row['evidence'])>1000:return ['invalid_evidence_array']
    pages={(m['material_id'],m['page_index']) for m in task['materials']};seen=set()
    for e in row['evidence']:
        if not isinstance(e,dict) or not {'material_id','page_index','fact'}<=set(e) or set(e)-{'material_id','page_index','fact','bbox','rule_quote'}:return ['invalid_evidence_fields']
        if not nonempty(e['material_id'],128) or not integer(e['page_index']):return ['invalid_evidence_identifier_or_page']
        if (e['material_id'],e['page_index']) not in pages:return ['evidence_material_page_not_in_input']
        if not nonempty(e['fact']):return ['blank_evidence_fact']
        if 'rule_quote' in e and not nonempty(e['rule_quote']):return ['blank_rule_quote']
        if 'bbox' in e and not bbox_ok(e['bbox']):return ['invalid_bbox']
        encoded=json.dumps(e,sort_keys=True,ensure_ascii=False)
        if encoded in seen:return ['duplicate_evidence']
        seen.add(encoded)
    if row['execution_status']=='completed' and visual_evidence_required(task,row['applicability']) and not row['evidence']:return ['required_visual_evidence_missing']
    return []

def annotation_errors(row,target,task):
    if not isinstance(row,dict):return ['annotation_not_object']
    fields={'annotation_id','audit_unit_id','material_id','page_index','value','status','label','bbox'}
    if set(row)!=fields:return ['annotation_fields']
    if any(not nonempty(row[k],256) for k in ['annotation_id','audit_unit_id','material_id']):return ['annotation_identifier']
    if any(row[k]!=target[k] for k in ['annotation_id','audit_unit_id','material_id']):return ['annotation_wrong_target']
    if not integer(row['page_index']):return ['annotation_page_type']
    if (row['material_id'],row['page_index']) not in {(m['material_id'],m['page_index']) for m in task['materials']}:return ['annotation_material_page_not_in_input']
    if not isinstance(row['status'],str) or row['status'] not in ['value','unreadable','invalid_value']:return ['annotation_status']
    if row['status']=='unreadable' and row['value'] is not None:return ['unreadable_requires_null']
    if not isinstance(row['label'],str) or row['label'] not in LABELS:return ['annotation_label']
    if not bbox_ok(row['bbox']):return ['annotation_bbox']
    return []
