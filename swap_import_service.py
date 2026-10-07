"""CSV imports into the independent swap payment and swap transaction tables."""
import csv
import hashlib
import re
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import psycopg
from psycopg import sql

SCHEMA = 'swap_reconciliation'
AUDIT_SCHEMA = 'swap_reconciliation'
TABLES = {'swap_payments': 'swap_payments', 'swap': 'swap_transactions'}
DATASETS = {'swap_payments': 'Swap payments', 'swap': 'Swapping transactions'}
BOOL_FIELDS = {'locked', 'manual_credit', 'manual_oem_entered'}


def normalize(value):
    return re.sub(r'[^a-z0-9]+', '_', value.strip().lower()).strip('_')


def layout(headers):
    if not headers or len(headers) != len(set(headers)):
        raise ValueError('CSV needs unique column headers.')
    normalized = [normalize(h) for h in headers]
    matches = []
    if {'record_id', 'atlas_transaction_id'} <= set(normalized): matches.append('swap_payments')
    if {'record_id', 'transaction_id', 'pay_method'} <= set(normalized): matches.append('swap')
    if len(matches) != 1:
        raise ValueError('Choose a payment export with Record Id and Atlas Transaction ID, or a swap export with Record Id, Transaction ID and Pay Method.')
    return matches[0]


def value(row, name):
    return next((v for k,v in row.items() if normalize(k) == normalize(name)), None)


def timestamp(text):
    if not text or not text.strip(): return None
    try:
        date = datetime.fromisoformat(text)
        return date.astimezone(timezone.utc).replace(tzinfo=None) if date.tzinfo else date
    except ValueError:
        return None


def version(row):
    dates = [timestamp(value(row,name)) for name in ('Last Modified On','Modified Time')]
    return max((d for d in dates if d is not None), default=None)


def boolean(text):
    if text is None or not text.strip(): return None
    if text.lower() in ('true','1','yes'): return True
    if text.lower() in ('false','0','no'): return False
    raise ValueError('Invalid boolean; use true or false.')


def read_rows(path, dataset=None):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        detected = layout(reader.fieldnames)
        if dataset is not None and dataset != detected:
            raise ValueError('The CSV module changed after validation.')
        for number,row in enumerate(reader,1):
            if None in row or any(v is None for v in row.values()):
                raise ValueError(f'Record {number} has the wrong number of fields.')
            if not (value(row,'Record Id') or '').strip():
                raise ValueError(f'Record {number} is missing Record Id.')
            for field,text in row.items():
                try:
                    if normalize(field) in BOOL_FIELDS: boolean(text)
                    if normalize(field) in ('amount','swap_amount') and text.strip():
                        if not Decimal(text).is_finite(): raise ValueError('Amount must be finite.')
                except (ValueError,InvalidOperation) as exc:
                    raise ValueError(f'Record {number}, {field}: {exc}') from None
            yield number,row


def inspect_csv(path, progress=None):
    with path.open(encoding='utf-8-sig',newline='') as stream:
        dataset=layout(next(csv.reader(stream),[]))
    ids=set();countries=Counter();duplicates=count=0;minimum=maximum=None
    for count,row in read_rows(path,dataset):
        identifier=value(row,'Record Id')
        duplicates += identifier in ids
        ids.add(identifier)
        countries[value(row,'Country') or 'Unknown country'] += 1
        date=timestamp(value(row,'Created On') or value(row,'Transaction Date'))
        if date is not None:
            minimum=min(minimum,date) if minimum else date
            maximum=max(maximum,date) if maximum else date
        if progress and count%10000==0:progress(count,'validating')
    if not count:raise ValueError('This CSV has headers but no data records.')
    return {'dataset':dataset,'module':DATASETS[dataset],'rows':count,
            'distinct_record_ids':len(ids),'duplicate_record_id_rows':duplicates,
            'countries':dict(countries),'min_created_on':minimum.isoformat() if minimum else None,
            'max_created_on':maximum.isoformat() if maximum else None,'export_cap_warning':count>=200000}


def column_mapping(headers, columns):
    mapping={}
    for header in headers:
        matches=[c for c in columns if c==header]
        if not matches:matches=[c for c in columns if normalize(c)==normalize(header)]
        if len(matches)>1:raise ValueError('Ambiguous header: '+header+'. Use original Zoho headers.')
        if not matches:continue
        column=matches[0]
        if column in mapping:raise ValueError('Repeated mapped column: '+column)
        mapping[column]=header
    if 'Record Id' not in mapping:raise ValueError('Record Id is required.')
    return mapping


def import_file(conn,path,dataset,sql_module=None,*,progress=None):
    with path.open(encoding='utf-8-sig',newline='') as stream:
        headers=next(csv.reader(stream),[])
    if layout(headers)!=dataset:raise ValueError('The CSV module changed after validation.')
    table=sql.Identifier(SCHEMA,TABLES[dataset])
    checksum=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):checksum.update(chunk)
    with conn.transaction():
        with conn.cursor() as cur:
            # Also serialize writers that do not use our upload service.
            cur.execute(sql.SQL('LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE').format(table))
            cur.execute('SELECT attname,format_type(atttypid,atttypmod) FROM pg_attribute WHERE attrelid=%s::regclass AND attnum>0 AND NOT attisdropped ORDER BY attnum',(SCHEMA+'.'+TABLES[dataset],))
            types=dict(cur.fetchall())
            mapping=column_mapping(headers,types)
            columns=list(mapping)
            names=sql.SQL(', ').join(map(sql.Identifier,columns))
            cur.execute(sql.SQL('CREATE TEMP TABLE swap_upload_stage (LIKE {} INCLUDING DEFAULTS, _upload_row bigint, _upload_version timestamp) ON COMMIT DROP').format(table))
            copy_names=sql.SQL(', ').join(map(sql.Identifier,columns+['_upload_row','_upload_version']))
            count=0;minimum=maximum=None
            with cur.copy(sql.SQL('COPY swap_upload_stage ({}) FROM STDIN').format(copy_names)) as copy:
                for count,row in read_rows(path,dataset):
                    values=[boolean(row[mapping[col]]) if types[col]=='boolean' else row[mapping[col]] if row[mapping[col]]!='' else None for col in columns]
                    copy.write_row(values+[count,version(row)])
                    date=timestamp(value(row,'Created On') or value(row,'Transaction Date'))
                    if date is not None:
                        minimum=min(minimum,date) if minimum else date
                        maximum=max(maximum,date) if maximum else date
                    if progress and count%10000==0:progress(count,'staging')
            cur.execute("""CREATE TEMP TABLE swap_upload_newest ON COMMIT DROP AS SELECT DISTINCT ON ("Record Id") * FROM swap_upload_stage ORDER BY "Record Id",coalesce(_upload_version,'-infinity'::timestamp) DESC,_upload_row DESC""")
            cur.execute(sql.SQL('SELECT 1 FROM {} t JOIN swap_upload_newest n ON t."Record Id"=n."Record Id" GROUP BY t."Record Id" HAVING count(*)>1 LIMIT 1').format(table))
            if cur.fetchone():raise ValueError('The target table contains repeated Record Ids. Resolve them before importing an overlapping export.')
            modified=[col for col in ('Last Modified On','Modified Time') if col in types]
            existing_columns=sql.SQL(', ').join(sql.SQL('t.{}').format(sql.Identifier(col)) for col in ['Record Id']+modified)
            cur.execute(sql.SQL('SELECT {} FROM {} t JOIN swap_upload_newest n ON t."Record Id"=n."Record Id"').format(existing_columns,table))
            versions=[(row[0],version(dict(zip(['Record Id']+modified,row)))) for row in cur.fetchall()]
            cur.execute('CREATE TEMP TABLE swap_upload_versions (record_id text PRIMARY KEY, modified_at timestamp) ON COMMIT DROP')
            with cur.copy('COPY swap_upload_versions (record_id,modified_at) FROM STDIN') as copy:
                for row in versions:copy.write_row(row)
            updates=[col for col in columns if col!='Record Id']
            assignments=sql.SQL(', ').join(sql.SQL('{}=n.{}').format(sql.Identifier(col),sql.Identifier(col)) for col in updates)
            old=sql.SQL(', ').join(sql.SQL('t.{}').format(sql.Identifier(col)) for col in updates)
            new=sql.SQL(', ').join(sql.SQL('n.{}').format(sql.Identifier(col)) for col in updates)
            if progress:progress(count,'merging')
            cur.execute(sql.SQL("""UPDATE {} t SET {} FROM swap_upload_newest n JOIN swap_upload_versions v ON v.record_id=n."Record Id" WHERE t."Record Id"=n."Record Id" AND coalesce(n._upload_version,'-infinity'::timestamp)>=coalesce(v.modified_at,'-infinity'::timestamp) AND ROW({}) IS DISTINCT FROM ROW({})""").format(table,assignments,old,new))
            applied=cur.rowcount
            selected=sql.SQL(', ').join(sql.SQL('n.{}').format(sql.Identifier(col)) for col in columns)
            cur.execute(sql.SQL('INSERT INTO {} ({}) SELECT {} FROM swap_upload_newest n WHERE NOT EXISTS (SELECT 1 FROM {} t WHERE t."Record Id"=n."Record Id")').format(table,names,selected,table))
            applied+=cur.rowcount
            cur.execute(sql.SQL('INSERT INTO {}.swap_import_batches (dataset,source_file,sha256,rows_read,rows_applied,min_created_on,max_created_on) VALUES (%s,%s,%s,%s,%s,%s,%s)').format(sql.Identifier(AUDIT_SCHEMA)),(dataset,path.name,checksum.hexdigest(),count,applied,minimum,maximum))
    return {'rows_read':count,'rows_applied':applied,'min_created_on':minimum.isoformat() if minimum else None,'max_created_on':maximum.isoformat() if maximum else None}


def source_inventory(settings):
    with psycopg.connect(**settings,connect_timeout=15) as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        sources=[]
        for dataset,table in TABLES.items():
            for country,count in conn.execute(sql.SQL('SELECT "Country",count(*) FROM {} GROUP BY "Country" ORDER BY "Country"').format(sql.Identifier(SCHEMA,table))):
                sources.append({'dataset':dataset,'module':DATASETS[dataset],'country':country,'rows':count,'first':None,'last':None})
        batches=[{'id':key,'module':DATASETS[dataset],'filename':filename,'rows_read':read,'rows_applied':applied,'imported_at':when.isoformat()}
                 for key,dataset,filename,read,applied,when in conn.execute(sql.SQL('SELECT batch_id,dataset,source_file,rows_read,rows_applied,imported_at FROM {}.swap_import_batches ORDER BY batch_id DESC LIMIT 10').format(sql.Identifier(AUDIT_SCHEMA)))]
    return {'sources':sources,'recent_imports':batches}
