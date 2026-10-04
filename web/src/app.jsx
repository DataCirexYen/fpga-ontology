import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  Alignment, BlueprintProvider, Breadcrumbs, Button, ButtonGroup, Callout,
  Classes, Dialog, DialogBody, DialogFooter, FileInput, FormGroup, HTMLSelect,
  HTMLTable, Icon, InputGroup, Navbar, NonIdealState, OverlayToaster,
  Position, Spinner, Tab, Tabs, TextArea, Tree,
} from '@blueprintjs/core';
import '@blueprintjs/core/lib/css/blueprint.css';
import './style.css';

const THEME_KEY = 'workbench-theme';
function savedTheme() {
  try { return localStorage.getItem(THEME_KEY) === 'light' ? 'light' : 'dark'; }
  catch { return 'dark'; }
}
function applyTheme(theme) {
  document.documentElement.classList.toggle(Classes.DARK, theme === 'dark');
}
applyTheme(savedTheme());

const pretty = value => JSON.stringify(value, null, 2);
const text = value => typeof value === 'object' ? JSON.stringify(value) : String(value ?? '');
const label = obj => text(obj.data.name || obj.data.title || obj.id);
const date = value => new Date(value).toLocaleString();
const actionTemplates = {
  update: { patch: { status: 'Complete' } },
  http: { url: 'http://127.0.0.1:9000/action' },
  record: { evidence_kind: 'inspection', parameters: [
    { name: 'result', label: 'Result', type: 'enum', values: ['PASS', 'FAIL'], required: true },
    { name: 'evidence_uri', label: 'Evidence URI', type: 'uri' },
    { name: 'notes', label: 'Notes', type: 'text' },
    { name: 'recorded_by', label: 'Recorded by', type: 'string', required: true } ],
    criteria: [{ kind: 'required_if', param: 'notes', when: { result: 'FAIL' }, message: 'Explain a failure in the notes.' }] },
};
const operationName = kind => ({ update: 'Property update', http: 'HTTP POST', record: 'Record evidence' })[kind] || kind;
const views = [
  ['objects', 'Objects', 'cube'], ['sources', 'Data sources', 'database'],
  ['ontology', 'Ontology', 'diagram-tree'], ['actions', 'Actions', 'lightning'],
  ['activity', 'Activity', 'history'],
];

async function api(path, body) {
  const response = await fetch(`/api/${path}`, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const result = await response.json();
  if (!response.ok || result.ok === false) throw new Error(result.error || 'Request failed');
  return result;
}

function Empty({ title, icon = 'inbox', children }) {
  return <NonIdealState className="empty-state" icon={icon} title={title} action={children} />;
}

function DataTable({ headers, children, className = '' }) {
  return <div className="table-scroll"><HTMLTable bordered striped className={`data-table ${className}`}>
    <thead><tr>{headers.map(h => <th key={h}>{h}</th>)}</tr></thead><tbody>{children}</tbody>
  </HTMLTable></div>;
}

function WorkspaceDialog({ spec, close, refresh, notify }) {
  const [values, setValues] = useState(spec.initial || {});
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [fileName, setFileName] = useState('Choose a file…');
  const set = (name, value) => setValues(previous => ({ ...previous, [name]: value }));
  const input = (name, title, options = {}) => <FormGroup label={title} labelFor={`field-${name}`} key={name}>
    {options.multiline ? <TextArea id={`field-${name}`} name={name} fill className="json-input" rows={8}
      required={options.required !== false} value={values[name] ?? ''} onChange={e => set(name, e.target.value)} spellCheck={false} /> :
      <InputGroup id={`field-${name}`} name={name} type={options.type || 'text'} autoFocus={options.autoFocus}
        required={options.required !== false} value={values[name] ?? ''} onChange={e => set(name, e.target.value)} />}
  </FormGroup>;
  const select = (name, title, options, onChange) => <FormGroup label={title} labelFor={`field-${name}`} key={name}>
    <HTMLSelect id={`field-${name}`} name={name} fill value={values[name]} options={options}
      onChange={e => { set(name, e.target.value); onChange?.(e.target.value); }} />
  </FormGroup>;
  let fields;
  if (spec.kind === 'type') fields = <>{input('name', 'Name', { autoFocus: true })}{input('description', 'Description', { required: false })}</>;
  if (spec.kind === 'object') fields = <>{!spec.object && input('id', 'Object ID', { autoFocus: true })}{input('data', 'Properties (JSON object)', { multiline: true })}</>;
  if (spec.kind === 'link') fields = <>{input('label', 'Relationship name', { autoFocus: true })}{select('target', 'Connect to', spec.targets.map((o, i) => ({ value: String(i), label: `${o.type} / ${o.id} — ${label(o)}` })))}</>;
  if (spec.kind === 'import') fields = <>
    <div className="form-columns">{input('name', 'Source name', { autoFocus: true })}{input('type', 'Object type')}</div>
    <div className="form-columns">{select('kind', 'Source format', [{ value: 'json', label: 'JSON' }, { value: 'csv', label: 'CSV' }, { value: 'http', label: 'HTTP JSON endpoint' }])}{input('id_field', 'Unique ID field')}</div>
    {values.kind === 'http' ? input('url', 'GET endpoint', { type: 'url' }) : <>
      <FormGroup label="File" labelFor="source-file">
        <FileInput text={fileName} fill hasSelection={fileName !== 'Choose a file…'} inputProps={{ id: 'source-file', accept: values.kind === 'csv' ? '.csv' : '.json' }}
          onInputChange={async e => {
            const file = e.target.files[0];
            if (!file) return;
            try {
              if (file.size > 5 * 1024 * 1024) throw new Error('File exceeds 5 MB');
              set('content', await file.text()); setFileName(file.name); setError('');
            } catch (e) { setError(e.message); }
          }} />
      </FormGroup>
      {input('content', 'Data', { multiline: true })}
    </>}
    <div className="form-note">Matching IDs replace existing properties, including local edits. Missing rows are kept. Maximum 5 MB.</div>
  </>;
  if (spec.kind === 'action') fields = <>
    {input('name', 'Action name', { autoFocus: true })}
    {select('type', 'Object type', spec.types.map(t => ({ value: t.name, label: t.name })))}
    {select('kind', 'Operation', [{ value: 'update', label: 'Update local properties' }, { value: 'http', label: 'POST object to a system' }, { value: 'record', label: 'Record evidence (typed parameters, criteria, immutable record)' }],
      kind => set('config', pretty(actionTemplates[kind])))}
    {input('config', 'Configuration (JSON)', { multiline: true })}
    {values.kind === 'record' && <div className="form-note">Parameter types: string, text, number, enum (values), object (object_type, link), uri, date. Criteria: max_from_property, required_if, transition, requires_latest_evidence, target_property. Running the action never edits the target; it creates an EvidenceRecord linked to it.</div>}
  </>;
  const paramField = param => {
    const name = param.name, title = param.label || name, required = !!param.required;
    const id = `param-${name}`;
    if (param.type === 'enum') return <FormGroup label={title} labelFor={id} labelInfo={required ? '(required)' : ''} key={name}>
      <HTMLSelect id={id} name={name} fill required={required} value={values[name] ?? ''} onChange={e => set(name, e.target.value)}
        options={[{ value: '', label: 'Select…' }, ...param.values.map(v => ({ value: v, label: v }))]} /></FormGroup>;
    if (param.type === 'object') {
      const choices = (spec.objects || []).filter(o => o.type === param.object_type);
      return <FormGroup label={title} labelFor={id} labelInfo={required ? '(required)' : ''} helperText={`Links the record to a ${param.object_type}`} key={name}>
        <HTMLSelect id={id} name={name} fill required={required} value={values[name] ?? ''} onChange={e => set(name, e.target.value)}
          options={[{ value: '', label: `Select a ${param.object_type}…` }, ...choices.map(o => ({ value: o.id, label: `${o.id} — ${label(o)}` }))]} /></FormGroup>;
    }
    if (param.type === 'text') return <FormGroup label={title} labelFor={id} labelInfo={required ? '(required)' : ''} key={name}>
      <TextArea id={id} name={name} fill rows={3} required={required} value={values[name] ?? ''} onChange={e => set(name, e.target.value)} /></FormGroup>;
    const type = { number: 'number', uri: 'url', date: 'date' }[param.type] || 'text';
    return <FormGroup label={title} labelFor={id} labelInfo={required ? '(required)' : ''} key={name}>
      <InputGroup id={id} name={name} type={type} step={type === 'number' ? 'any' : undefined} required={required}
        value={values[name] ?? ''} onChange={e => set(name, e.target.value)} /></FormGroup>;
  };
  if (spec.kind === 'run') fields = <>
    <FormGroup label="Object"><code>{spec.object.type} / {spec.object.id}</code> <span className="form-note-inline">{label(spec.object)}</span></FormGroup>
    {spec.action.kind === 'record' ? <>
      {spec.action.config.parameters.map(paramField)}
      {spec.action.config.criteria?.length > 0 && <div className="form-note">Submission criteria: {spec.action.config.criteria.map(c => c.message || c.kind).join(' · ')}</div>}
      <div className="form-note">Creates an immutable <code>EvidenceRecord</code> of kind <code>{spec.action.config.evidence_kind}</code> linked to this object. The object itself is not edited.</div>
    </> : <>
      <FormGroup label={spec.action.kind === 'http' ? 'HTTP POST configuration' : 'Property update'}><pre>{pretty(spec.action.config)}</pre></FormGroup>
      {spec.action.kind === 'http' && <div className="form-note">Sends this object to the endpoint. External side effects cannot be undone here.</div>}
    </>}
  </>;

  async function submit(event) {
    event.preventDefault(); setBusy(true); setError('');
    try { await spec.save(values); }
    catch (e) { setError(e.message); setBusy(false); return; }
    close();
    try { await refresh(); notify(spec.kind === 'run' ? 'Action completed' : 'Saved to local workspace'); }
    catch (e) { notify(`Saved, but reload failed: ${e.message}`, 'danger'); }
  }

  return <Dialog isOpen title={spec.title} icon={spec.icon} onClose={close} className="workspace-dialog"
    canEscapeKeyClose={!busy} canOutsideClickClose={!busy} isCloseButtonShown={!busy}>
    <form id="workspace-form" onSubmit={submit}>
      <DialogBody>{fields}{error && <Callout intent="danger" role="alert" id="form-error">{error}</Callout>}</DialogBody>
      <DialogFooter actions={<><Button text="Cancel" onClick={close} disabled={busy} /><Button type="submit" intent="primary" loading={busy} text={spec.submit || 'Save'} /></>} />
    </form>
  </Dialog>;
}

function App() {
  const [theme, setTheme] = useState(savedTheme);
  const [state, setState] = useState(null);
  const [loadError, setLoadError] = useState('');
  const [view, setView] = useState('objects');
  const [type, setType] = useState('');
  const [selected, setSelected] = useState('');
  const [query, setQuery] = useState('');
  const [detailTab, setDetailTab] = useState('properties');
  const [dialog, setDialog] = useState(null);
  const [pending, setPending] = useState('');
  const toaster = useRef(null);
  const notify = (message, intent = 'success') => toaster.current?.show({ message, intent, timeout: 5000 });

  function toggleTheme() {
    const next = theme === 'dark' ? 'light' : 'dark';
    applyTheme(next);
    setTheme(next);
    try { localStorage.setItem(THEME_KEY, next); } catch { /* Theme still works without storage. */ }
  }

  async function refresh() {
    const data = await api('state');
    setState(data); setLoadError('');
    setType(current => data.types.some(t => t.name === current) ? current : data.types[0]?.name || '');
  }
  useEffect(() => { refresh().catch(e => setLoadError(e.message)); }, []);
  async function perform(key, path, body, message) {
    setPending(key);
    try { await api(path, body); await refresh(); notify(message); }
    catch (e) { notify(e.message, 'danger'); }
    finally { setPending(''); }
  }
  function browse(nextType, id = '') { setType(nextType); setSelected(id); setQuery(''); setView('objects'); setDetailTab('properties'); }
  function importDialog() {
    setDialog({ kind: 'import', title: 'Import a data source', icon: 'import', submit: 'Import',
      initial: { name: '', type: type || 'Asset', kind: 'json', id_field: 'id', content: '', url: 'http://127.0.0.1:9000/assets' },
      save: async d => { await api('import', d); browse(d.type.trim()); } });
  }
  function typeDialog() {
    setDialog({ kind: 'type', title: 'Create object type', icon: 'cube-add', initial: { name: '', description: '' },
      save: async d => { await api('types', d); browse(d.name.trim()); } });
  }
  function objectDialog(object) {
    setDialog({ kind: 'object', object, title: object ? 'Edit properties' : `New ${type}`, icon: 'edit',
      initial: { id: '', data: pretty(object?.data || { name: '' }) },
      save: async d => { const id = object?.id || d.id; await api('objects', { type, id, data: JSON.parse(d.data) }); setSelected(id.trim()); } });
  }
  function linkDialog(object) {
    const targets = state.objects.filter(o => o.type !== object.type || o.id !== object.id);
    if (!targets.length) return notify('Create another object to link first.', 'warning');
    setDialog({ kind: 'link', title: 'Add relationship', icon: 'link', targets, initial: { label: 'relates to', target: '0' },
      save: d => { const target = targets[Number(d.target)]; return api('links', { label: d.label, from_type: object.type, from_id: object.id, to_type: target.type, to_id: target.id }); } });
  }
  function actionDialog() {
    if (!state.types.length) return notify('Create an object type first.', 'warning');
    setDialog({ kind: 'action', title: 'Create action', icon: 'lightning', types: state.types,
      initial: { name: '', type: type || state.types[0].name, kind: 'update', config: pretty({ patch: { status: 'Complete' } }) },
      save: d => api('actions', { ...d, config: JSON.parse(d.config) }) });
  }
  function runDialog(action, object) {
    const initial = Object.fromEntries((action.kind === 'record' ? action.config.parameters : []).map(p => [p.name, p.default ?? '']));
    setDialog({ kind: 'run', title: action.name, icon: 'play', submit: action.kind === 'record' ? 'Record' : 'Run action', action, object, objects: state.objects, initial,
      save: values => api('run', { action: action.id, id: object.id, params: action.kind === 'record' ? values : undefined }) });
  }

  const currentView = views.find(v => v[0] === view);
  const objects = state?.objects.filter(o => o.type === type) || [];
  const filtered = objects.filter(o => `${o.id} ${JSON.stringify(o.data)}`.toLowerCase().includes(query.toLowerCase()));
  const object = objects.find(o => o.id === selected) || objects[0];
  const properties = [...new Set(objects.flatMap(o => Object.keys(o.data)))].filter(k => k !== 'id');
  const source = state?.sources.find(s => s.id === object?.source);
  const links = state?.links.filter(l => object && ((l.from_type === type && l.from_id === object.id) || (l.to_type === type && l.to_id === object.id))) || [];
  const actions = state?.actions.filter(a => a.type === type) || [];
  const evidence = object ? links.filter(l => l.label === 'evidences' && l.from_type === 'EvidenceRecord' && l.to_type === type && l.to_id === object.id)
    .map(l => state.objects.find(o => o.type === 'EvidenceRecord' && o.id === l.from_id)).filter(Boolean).sort((a, b) => b.id.localeCompare(a.id)) : [];
  const currentState = Object.values(evidence.reduce((acc, e) => { acc[e.data.kind] = acc[e.data.kind] || e; return acc; }, {}));
  const evidenceFields = e => Object.entries(e.data).filter(([k]) => !['kind', 'action', 'target_type', 'target_id', 'recorded_at'].includes(k));

  function renderDetails() {
    if (!object) return <Empty title="No object selected" icon="cube" />;
    return <>
      <div className="detail-heading"><h2>{label(object)}</h2></div>
      <div className="object-identity"><code>{object.type} / {object.id}</code></div>
      <Tabs id="object-tabs" selectedTabId={detailTab} onChange={setDetailTab} renderActiveTabPanelOnly>
        <Tab id="properties" title="Properties" panel={<>
          <dl className="properties">{Object.entries(object.data).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{text(value)}</dd></div>)}</dl>
          <Button icon="edit" text="Edit properties" onClick={() => objectDialog(object)} />
          <dl className="properties provenance"><div><dt>Source</dt><dd>{source?.name || 'Workspace'}</dd></div><div><dt>Updated</dt><dd>{date(object.updated)}</dd></div></dl>
        </>} />
        <Tab id="relationships" title="Relationships" panel={<>
          <div className="relationships">{links.map(l => {
            const forward = l.from_type === type && l.from_id === object.id;
            const targetType = forward ? l.to_type : l.from_type, targetId = forward ? l.to_id : l.from_id;
            return <div key={l.id} className="relationship"><div>{l.label} <Icon icon={forward ? 'arrow-right' : 'arrow-left'} size={12} /></div>
              <Button minimal icon="cube" data-link={l.id} text={`${targetType} / ${targetId}`} onClick={() => browse(targetType, targetId)} /></div>;
          })}{!links.length && <div className="empty-inline">No relationships</div>}</div>
          <Button icon="link" text="Add relationship" onClick={() => linkDialog(object)} />
        </>} />
        <Tab id="evidence" title={`Evidence${evidence.length ? ` (${evidence.length})` : ''}`} panel={<div className="evidence-panel">
          {currentState.length > 0 && <><h3 className="section-subtitle">Current state</h3>
            <dl className="properties">{currentState.map(e => <div key={e.data.kind}><dt>{e.data.kind}</dt><dd>{evidenceFields(e).map(([k, v]) => `${k}: ${text(v)}`).join(' · ')}</dd></div>)}</dl></>}
          <h3 className="section-subtitle">History</h3>
          {evidence.length ? <div className="evidence-list">{evidence.map(e => <details key={e.id} className="activity-event"><summary>
            <span>{e.data.kind}</span><span className="event-subject">{e.data.action}</span><time>{date(e.data.recorded_at)}</time></summary>
            <dl className="properties">{evidenceFields(e).map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{text(v)}</dd></div>)}</dl>
            <Button minimal small icon="cube" text={`EvidenceRecord / ${e.id}`} onClick={() => browse('EvidenceRecord', e.id)} />
          </details>)}</div> : <div className="empty-inline">No evidence recorded. Imported properties stay as the source delivered them; actions add evidence here.</div>}
        </div>} />
        <Tab id="actions" title="Actions" panel={<div className="object-actions">{actions.length ? actions.map(a =>
          <Button key={a.id} icon="play" alignText="left" text={a.name} onClick={() => runDialog(a, object)} />
        ) : <><div className="empty-inline">No actions for {type}</div><Button icon="add" text="New action" onClick={actionDialog} /></>}</div>} />
      </Tabs>
    </>;
  }

  function renderObjects() {
    if (!type) return <Empty title="No objects yet" icon="cube"><ButtonGroup>
      <Button intent="primary" text="Import your data" onClick={importDialog} />
      <Button text="Try example workspace" loading={pending === 'demo'} onClick={() => perform('demo', 'demo', {}, 'Example workspace loaded')} />
    </ButtonGroup></Empty>;
    return <div className="object-workspace">
      <section className="records" aria-label="Objects">
        <div className="table-toolbar"><InputGroup id="search" leftIcon="search" aria-label="Search objects" placeholder={`Search ${type}…`} value={query} onChange={e => setQuery(e.target.value)}
          rightElement={query ? <Button minimal icon="cross" aria-label="Clear search" onClick={() => setQuery('')} /> : undefined} />
          <span className="record-count">{filtered.length} records</span></div>
        <DataTable headers={['Object ID', ...properties]} className="object-table">
          {filtered.map(o => <tr key={o.id} className={o.id === object?.id ? 'selected-row' : ''}>
            <td><Button minimal small intent="primary" text={o.id} onClick={() => { setSelected(o.id); setDetailTab('properties'); }} /></td>
            {properties.map(k => <td key={k} title={text(o.data[k])}>{text(o.data[k])}</td>)}
          </tr>)}
        </DataTable>
        {!filtered.length && <Empty title={query ? 'No matching objects' : 'No records'} icon="search" />}
      </section>
      <aside id="detail" className="object-detail" aria-label="Object details">{renderDetails()}</aside>
    </div>;
  }

  function renderSources() {
    if (!state.sources.length) return <Empty title="No data sources" icon="database"><Button intent="primary" text="Import a source" onClick={importDialog} /></Empty>;
    return <DataTable headers={['Source', 'Format', 'Object type', 'Rows', 'Last import', 'Operations']}>
      {state.sources.map(s => <tr key={s.id}><td><strong>{s.name}</strong>{s.config.url && <div className="endpoint">{s.config.url}</div>}</td><td>{s.config.kind.toUpperCase()}</td><td>{s.type}</td><td>{s.count}</td><td>{date(s.updated)}</td><td>
        <ButtonGroup minimal>{s.config.kind === 'http' && <Button icon="refresh" text="Refresh" loading={pending === s.id} disabled={!!pending && pending !== s.id} onClick={() => perform(s.id, 'refresh', { id: s.id }, 'Source refreshed')} />}
          <Button icon="arrow-right" text="Browse objects" onClick={() => browse(s.type)} /></ButtonGroup>
      </td></tr>)}
    </DataTable>;
  }

  function renderOntology() {
    if (!state.types.length) return <Empty title="No object types" icon="diagram-tree"><Button text="Create object type" intent="primary" onClick={typeDialog} /></Empty>;
    return <>
      <DataTable headers={['Object type', 'Objects', 'Observed properties']}>
        {state.types.map(t => {
          const items = state.objects.filter(o => o.type === t.name);
          const keys = [...new Set(items.flatMap(o => Object.keys(o.data)))];
          return <tr key={t.name}><td><Button minimal icon="cube" text={t.name} onClick={() => browse(t.name)} />{t.description && <div className="type-description">{t.description}</div>}</td><td>{items.length}</td><td>
            <dl className="schema-properties">{keys.map(k => <div key={k}><dt>{k}</dt><dd>{[...new Set(items.filter(o => k in o.data).map(o => o.data[k] === null ? 'null' : Array.isArray(o.data[k]) ? 'array' : typeof o.data[k]))].join(' | ')}</dd></div>)}</dl>
            {!keys.length && '—'}
          </td></tr>;
        })}
      </DataTable>
      <h2 className="section-title">Connections</h2>
      {state.links.length ? <DataTable headers={['From', 'Relationship', 'To']}>
        {state.links.map(l => <tr key={l.id}>
          <td><Button minimal text={`${l.from_type} / ${l.from_id}`} onClick={() => browse(l.from_type, l.from_id)} /></td>
          <td>{l.label}</td><td><Button minimal text={`${l.to_type} / ${l.to_id}`} onClick={() => browse(l.to_type, l.to_id)} /></td>
        </tr>)}
      </DataTable> : <div className="empty-inline">No connections</div>}
    </>;
  }

  function renderActions() {
    if (!state.actions.length) return <Empty title="No actions" icon="lightning"><Button text="Create action" intent="primary" onClick={actionDialog} /></Empty>;
    return <DataTable headers={['Action', 'Object type', 'Operation', 'Configuration', '']}>
      {state.actions.map(a => <tr key={a.id}><td><strong>{a.name}</strong></td><td>{a.type}</td><td>{operationName(a.kind)}</td><td><pre className="config-preview">{pretty(a.config)}</pre></td><td><Button minimal icon="arrow-right" text="Browse objects" onClick={() => { browse(a.type); setDetailTab('actions'); }} /></td></tr>)}
    </DataTable>;
  }

  function renderActivity() {
    if (!state.events.length) return <Empty title="No activity" icon="history" />;
    return <div className="activity-list">{state.events.map(e => <details key={e.id} className="activity-event"><summary>
      <span>{e.kind}</span><span className="event-subject">{text(e.detail.action || e.detail.source || e.detail.name || [e.detail.type, e.detail.id].filter(Boolean).join(' / '))}</span><time>{date(e.time)}</time>
    </summary><pre>{pretty(e.detail)}</pre></details>)}</div>;
  }

  return <>
    <Navbar className={`app-navbar ${Classes.DARK}`}>
      <Navbar.Group><Navbar.Heading><span className="brand">FPGA</span></Navbar.Heading><Navbar.Divider /><span className="workspace-name">Ontology</span></Navbar.Group>
      <Navbar.Group align={Alignment.RIGHT} className="navbar-actions">
        <Button minimal icon={theme === 'dark' ? 'lightbulb' : 'moon'} aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`} title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`} onClick={toggleTheme} />
        <Button icon="import" intent="primary" text="Import data" onClick={importDialog} />
      </Navbar.Group>
    </Navbar>
    <div className="app-layout">
      <aside className="sidebar">
        <nav aria-label="Workspace">{views.map(([id, title, icon]) => <Button key={id} data-view={id} minimal fill alignText="left" icon={icon} text={title} active={view === id} intent={view === id ? 'primary' : 'none'} onClick={() => { setView(id); setQuery(''); }} />)}</nav>
        <div className="type-heading"><h2>Object types</h2><Button id="add-type" minimal small icon="add" aria-label="Create object type" onClick={typeDialog} /></div>
        <Tree id="types" contents={(state?.types || []).map(t => ({ id: t.name, label: t.name, icon: 'cube', isSelected: view === 'objects' && type === t.name,
          secondaryLabel: <span className="type-count">{state.objects.filter(o => o.type === t.name).length}</span> }))} onNodeClick={node => browse(String(node.id))} />
      </aside>
      <main>
        <Breadcrumbs items={[{ text: 'Workspace', onClick: () => setView('objects') }, { text: currentView[1] }, ...(view === 'objects' && type ? [{ text: type }] : [])]} />
        <div className="page-heading"><h1 id="title">{view === 'objects' ? type || 'Objects' : currentView[1]}</h1><div>
          {view === 'objects' && type && <Button icon="add" text="New object" onClick={() => objectDialog()} />}
          {view === 'ontology' && <Button icon="add" text="New object type" onClick={typeDialog} />}
          {view === 'actions' && <Button icon="add" text="New action" onClick={actionDialog} disabled={!state} />}
        </div></div>
        <div id="content">{loadError ? <Callout intent="danger" title="Could not load workspace">{loadError}<Button text="Retry" onClick={() => refresh().catch(e => setLoadError(e.message))} /></Callout> :
          !state ? <Spinner aria-label="Loading workspace" /> : ({ objects: renderObjects, sources: renderSources, ontology: renderOntology, actions: renderActions, activity: renderActivity })[view]()}</div>
      </main>
    </div>
    <OverlayToaster ref={toaster} position={Position.BOTTOM_RIGHT} maxToasts={3} />
    {dialog && <WorkspaceDialog spec={dialog} close={() => setDialog(null)} refresh={refresh} notify={notify} />}
  </>;
}

createRoot(document.getElementById('root')).render(<BlueprintProvider><App /></BlueprintProvider>);
