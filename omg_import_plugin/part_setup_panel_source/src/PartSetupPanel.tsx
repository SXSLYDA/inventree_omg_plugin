import { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, Anchor, Autocomplete, Badge, Button, Checkbox, Group, Image, Loader, MultiSelect, Select, Stack, Stepper, Table, Text, TextInput, Title } from '@mantine/core';
import { notifications } from '@mantine/notifications';

import { checkPluginVersion, type InvenTreePluginContext } from '@inventreedb/ui';

// Three panels on a part's page (backend: part_setup.py, accessory_setup.py):
//   OMG Part Setup  - pick a Component Type, fill in every parameter it needs
//   OMG Cavities    - a connector's Cavity Map as a grid + its related
//                     contacts / seals / blanks by series
//   OMG Accessories - on a connector: which locks / boots / covers fit and
//                     which are required; on an accessory: what it fits
// What each type needs, template names, AWG sizes and colours come from OMG.

const PLUGIN = '/plugin/omg-harness-import';
// Every panel call reaches OMG (part profiles, rules) - often longer than the
// 5s default of InvenTree's API client ("timeout of 5000ms exceeded").
const OMG_TIMEOUT_MS = 60000;
const LONG = { timeout: OMG_TIMEOUT_MS };

// ==================== Wire sizes: mm² and AWG ====================
// A contact's / terminal's Min / Max Gauge can be written either way: "0.5"
// (a bare number is mm²) or "20 AWG". OMG compares them in mm² - these show
// the same size in the other unit, from OMG's AWG / mm² table.

const GAUGE_RANGE_ROLES = ['contact.min_gauge', 'contact.max_gauge'];

function gaugeBoth(raw: string | undefined | null, table: Record<string, string | number>): string {
    const text = String(raw ?? '').trim();
    if (!text) return '';
    const isAwg = /awg|#|\/0/i.test(text);
    const number = parseFloat(text.replace(',', '.').replace(/^[^\d]*/, ''));
    if (Number.isNaN(number)) return '';
    if (isAwg) {
        const awg = text.match(/(\d\/0|\d+)/)?.[1] || '';
        const mm2 = table[awg];
        return mm2 !== undefined ? `${awg} AWG = ${Number(mm2)} mm²` : `${awg} AWG`;
    }
    // nearest AWG size in the table
    let best = '', diff = Infinity;
    for (const [awg, mm2] of Object.entries(table)) {
        const d = Math.abs(Number(mm2) - number);
        if (d < diff) { diff = d; best = awg; }
    }
    return best ? `${number} mm² ≈ ${best} AWG` : `${number} mm²`;
}

function gaugeRange(min: string | undefined, max: string | undefined, table: Record<string, string | number>): string {
    const lo = gaugeBoth(min, table), hi = gaugeBoth(max, table);
    if (!lo && !hi) return '';
    return lo === hi ? lo : `${lo || '?'} to ${hi || '?'}`;
}

// ==================== Parts table (like InvenTree's own part lists) ====================
// Picture, part number (opens the part), IPN and description, plus a tick box
// and a search box - for picking related parts and accessories.

interface TablePart { pk: number; name: string; ipn?: string; description?: string; thumbnail?: string; }

function PartNameLink({ context, part, panel = '' }: { context: InvenTreePluginContext; part: TablePart; panel?: string }) {
    const path = `/part/${part.pk}/${panel}`;
    return (
        <Anchor size="sm" href={`/web${path}`} onClick={(e: React.MouseEvent) => {
            if (e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;
            e.preventDefault();
            context.navigate(path);
        }}>{part.name}</Anchor>
    );
}

function PartsTable<T extends TablePart>({ context, parts, picked, onToggle, disabled, extra, extraTitle, search = true }: {
    context: InvenTreePluginContext;
    parts: T[];
    picked: Set<number>;
    onToggle: (pk: number, on: boolean) => void;
    disabled?: boolean;
    extra?: (p: T) => React.ReactNode;
    extraTitle?: string;
    search?: boolean;
}) {
    const [term, setTerm] = useState('');
    const words = term.toLowerCase().split(/\s+/).filter(Boolean);
    const shown = parts.filter((p) => {
        const text = `${p.name} ${p.ipn || ''} ${p.description || ''}`.toLowerCase();
        return words.every((w) => text.includes(w));
    });
    return (
        <Stack gap={4}>
            {search && parts.length > 5 && (
                <TextInput size="xs" placeholder="Search part number or description" value={term}
                    onChange={(e) => setTerm(e.currentTarget.value)} />
            )}
            <Table withTableBorder highlightOnHover verticalSpacing={4}>
                <Table.Thead><Table.Tr>
                    <Table.Th w={36} /><Table.Th w={44} /><Table.Th>Part</Table.Th><Table.Th>Description</Table.Th>
                    {extra && <Table.Th>{extraTitle || ''}</Table.Th>}
                </Table.Tr></Table.Thead>
                <Table.Tbody>
                    {shown.map((p) => (
                        <Table.Tr key={p.pk} style={{ cursor: disabled ? undefined : 'pointer' }}
                            onClick={(e) => {
                                if (disabled || (e.target as HTMLElement).closest('a, input')) return;
                                onToggle(p.pk, !picked.has(p.pk));
                            }}>
                            <Table.Td><Checkbox size="xs" checked={picked.has(p.pk)} disabled={disabled}
                                onChange={(e) => onToggle(p.pk, e.currentTarget.checked)} /></Table.Td>
                            <Table.Td>{p.thumbnail
                                ? <Image src={p.thumbnail} w={36} h={36} fit="contain" radius="sm" />
                                : <div style={{ width: 36, height: 36 }} />}</Table.Td>
                            <Table.Td><PartNameLink context={context} part={p} />
                                {p.ipn ? <Text size="xs" c="dimmed">{p.ipn}</Text> : null}</Table.Td>
                            <Table.Td><Text size="sm">{p.description || ''}</Text></Table.Td>
                            {extra && <Table.Td>{extra(p)}</Table.Td>}
                        </Table.Tr>
                    ))}
                    {shown.length === 0 && (
                        <Table.Tr><Table.Td colSpan={extra ? 5 : 4}><Text size="sm" c="dimmed">
                            {parts.length ? `Nothing matches "${term}".` : 'No parts.'}</Text></Table.Td></Table.Tr>
                    )}
                </Table.Tbody>
            </Table>
        </Stack>
    );
}

// ==================== This part's problems (OMG's Part Data Check) ====================
// The same check as the OMG Parts widget's "Needs setup" tab, for this one
// part. OMG keeps the last run; "Check again" asks it to re-check in the
// background (it reads every part from InvenTree) and this polls until done.

interface PartIssue { part: number; message: string; used_by: { harness: string; what: string }[]; used_count: number; }
interface PartCheck { key: string; title: string; issues: PartIssue[]; }
interface PartCheckState {
    report: { checks: PartCheck[] } | null;
    checked_at: string | null;
    running: boolean;
    error: string;
}

function PartDataCheck({ context, partId }: { context: InvenTreePluginContext; partId: number }) {
    const [stored, setStored] = useState<PartCheckState | null>(null);

    const load = useCallback(async (refresh = false) => {
        try {
            const params: Record<string, string | number> = { part: partId };
            if (refresh) params.refresh = 1;
            const response = await context.api.get(`${PLUGIN}/data-check/`, { params, ...LONG });
            setStored(response.data);
        } catch {
            setStored(null);
        }
    }, [context.api, partId]);

    useEffect(() => { load(); }, [load]);
    useEffect(() => {
        if (!stored?.running) return;
        const timer = setTimeout(() => load(), 3000);
        return () => clearTimeout(timer);
    }, [stored, load]);

    if (!stored) return null;
    const issues = (stored.report?.checks || []).flatMap((c) => c.issues.map((i) => ({ ...i, title: c.title })));
    const when = stored.checked_at ? new Date(stored.checked_at).toLocaleString() : null;
    const used = issues.find((i) => i.used_count);
    const recheck = (
        <Button size="compact-xs" variant="subtle" onClick={() => load(true)} loading={stored.running}>Check again</Button>
    );

    if (stored.running && !issues.length) {
        return <Group gap="xs"><Loader size="xs" /><Text size="xs" c="dimmed">OMG is checking every part...</Text></Group>;
    }
    if (!stored.report) {
        return <Group gap="xs"><Text size="xs" c="dimmed">{stored.error || "OMG hasn't checked parts yet."}</Text>{recheck}</Group>;
    }
    if (!issues.length) {
        return (
            <Group gap="xs">
                <Badge color="green" variant="light">OMG data check: OK</Badge>
                <Text size="xs" c="dimmed">{when ? `checked ${when}` : ''}</Text>{recheck}
            </Group>
        );
    }
    return (
        <Alert color="orange" title={`OMG data check: ${issues.length} problem${issues.length === 1 ? '' : 's'} on this part`}>
            <Stack gap={2}>
                {issues.map((i) => <Text key={`${i.title}-${i.message}`} size="xs"><b>{i.title}:</b> {i.message}</Text>)}
                {used && (
                    <Text size="xs" c="dimmed">
                        Used on {used.used_by.slice(0, 3).map((u) => `${u.harness} (${u.what})`).join(', ')}
                        {used.used_count > 3 ? ` and ${used.used_count - 3} more` : ''}.
                    </Text>
                )}
                <Group gap="xs">
                    <Text size="xs" c="dimmed">{when ? `Checked ${when} - fix below, then check again.` : ''}</Text>{recheck}
                </Group>
            </Stack>
        </Alert>
    );
}

function apiError(error: any, fallback: string): string {
    const data = error?.response?.data;
    if (data?.detail) return String(data.detail);
    if (data?.errors && Array.isArray(data.errors)) return data.errors.join(' ');
    return error?.message || fallback;
}

// ==================== Part Setup ====================

interface FieldInfo {
    template: string;
    label: string;
    units: string;
    exists: boolean;
    choices: string[];
    suggestions: string[];   // OMG's selection list, not enforced on the template
    per_item: boolean;       // can hold several values ("DT, DTM")
    value: string | null;
}

interface Profile {
    required: string[];
    optional: string[];
}

interface SetupState {
    error: string | null;
    can_edit: boolean;
    part: { pk: number; name: string; ipn: string; description: string };
    component_type: string;
    profile: string | null;
    profiles: Record<string, Profile>;
    fields: Record<string, FieldInfo>;
    missing_required: string[];
    standard_awg: string[];
    awg_to_mm2: Record<string, string | number>;
    colours: string[];
    colour_roles: string[];
    awg_roles: string[];
    accessory_types: string[];
}

function OMGPartSetupPanel({ context }: { context: InvenTreePluginContext }) {
    const partId = context.id;
    const [state, setState] = useState<SetupState | null>(null);
    const [loading, setLoading] = useState(true);
    const [componentType, setComponentType] = useState<string>('');
    const [values, setValues] = useState<Record<string, string>>({});
    const [errors, setErrors] = useState<Record<string, string>>({});
    const [saving, setSaving] = useState(false);

    const apply = useCallback((data: SetupState) => {
        setState(data);
        const profileKey = Object.keys(data.profiles || {}).find(
            (k) => k.toLowerCase() === (data.component_type || '').toLowerCase()) || '';
        setComponentType(profileKey || data.component_type || '');
        const v: Record<string, string> = {};
        for (const [role, field] of Object.entries(data.fields || {})) v[role] = field.value ?? '';
        setValues(v);
        setErrors({});
    }, []);

    const load = useCallback(async (refresh = false) => {
        setLoading(true);
        try {
            const response = await context.api.get(`${PLUGIN}/part-setup/${partId}/`,
                { params: refresh ? { refresh: '1' } : {}, ...LONG });
            apply(response.data);
        } catch (error: any) {
            setState(null);
            notifications.show({ color: 'red', title: 'OMG Part Setup', message: apiError(error, "Couldn't load.") });
        } finally {
            setLoading(false);
        }
    }, [context.api, partId, apply]);

    useEffect(() => { load(); }, [load]);

    const profile: Profile | null = state && componentType ? state.profiles[componentType] || null : null;
    // Steps: 1 what the part is (saved first - the OMG Cavities / Accessories
    // panels only appear on a part InvenTree knows is a connector), 2 its
    // parameters, 3 (connectors) cavities and accessories on their own panels.
    const typeSaved = !!state && !!componentType && state.profile === componentType;
    const [step, setStep] = useState<number | null>(null);
    // The page's panel list was made before the type was saved - open the
    // cavity / accessory panels with a page load so InvenTree adds them.
    const [typeJustSaved, setTypeJustSaved] = useState(false);
    useEffect(() => {
        if (state && step === null) setStep(typeSaved ? 1 : 0);
    }, [state, step, typeSaved]);

    const saveType = useCallback(async () => {
        if (!componentType) return;
        if (typeSaved) { setStep(1); return; }
        setSaving(true);
        try {
            const response = await context.api.post(`${PLUGIN}/part-setup/${partId}/`,
                { component_type: componentType, values: {} }, LONG);
            apply(response.data);
            setTypeJustSaved(true);
            setStep(1);
            notifications.show({ color: 'green', title: 'OMG Part Setup', message: `Saved as ${componentType}.` });
        } catch (error: any) {
            setErrors(error?.response?.data?.errors || {});
            notifications.show({ color: 'red', title: 'OMG Part Setup', message: apiError(error, 'Save failed.') });
        } finally {
            setSaving(false);
        }
    }, [componentType, typeSaved, context.api, partId, apply]);

    const openPanel = (panel: string) => {
        if (typeJustSaved) window.location.assign(`/web/part/${partId}/${panel}`);
        else context.navigate(`/part/${partId}/${panel}`);
    };

    const save = useCallback(async () => {
        if (!state || !profile) return;
        setSaving(true);
        const roles = [...profile.required, ...profile.optional];
        const payload: Record<string, string> = {};
        for (const role of roles) payload[role] = values[role] ?? '';
        try {
            const response = await context.api.post(`${PLUGIN}/part-setup/${partId}/`,
                { component_type: componentType, values: payload }, LONG);
            apply(response.data);
            notifications.show({ color: 'green', title: 'OMG Part Setup', message: 'Saved.' });
            if (componentType === 'Connector') setStep(2);
        } catch (error: any) {
            setErrors(error?.response?.data?.errors || {});
            notifications.show({ color: 'red', title: 'OMG Part Setup', message: apiError(error, 'Save failed.') });
        } finally {
            setSaving(false);
        }
    }, [state, profile, values, componentType, context.api, partId, apply]);

    if (loading) return <Loader size="sm" />;
    if (!state) return <Alert color="red">Couldn't load the part setup.</Alert>;
    if (state.error) return <Alert color="orange" title="OMG Part Setup">{state.error}</Alert>;

    // Set on their own panels, where they're picked from the parts that fit
    // (accessories) or edited as a grid with the related contacts (cavities).
    const MANAGED: Record<string, { panel: string; button: string }> = componentType === 'Connector' ? {
        'connector.cavity_map': { panel: 'omg-cavities', button: 'Edit on OMG Cavities' },
        'connector.contact_count': { panel: 'omg-cavities', button: 'Set from the Cavity Map (OMG Cavities)' },
        'connector.required_accessories': { panel: 'omg-accessories', button: 'Choose on OMG Accessories' },
        'connector.preferred_accessories': { panel: 'omg-accessories', button: 'Choose on OMG Accessories' },
    } : {};

    const fieldInput = (role: string) => {
        const field = state.fields[role];
        if (!field) return null;
        if (MANAGED[role]) {
            const required = profile?.required.includes(role);
            return (
                <Stack key={role} gap={2}>
                    <Text size="sm" fw={500}>{field.template}{required ? ' *' : ''}</Text>
                    <Text size="xs" c="dimmed">{field.label}</Text>
                    <Group gap="xs">
                        <Text size="sm" c={values[role] ? undefined : 'dimmed'}>{values[role] || '(not set)'}</Text>
                        {step === 2
                            ? <Button size="compact-xs" variant="light" onClick={() => openPanel(MANAGED[role].panel)}>
                                {MANAGED[role].button}</Button>
                            : <Text size="xs" c="dimmed">- set in step 3</Text>}
                    </Group>
                    {errors[role] && <Text size="xs" c="red">{errors[role]}</Text>}
                </Stack>
            );
        }
        const value = values[role] ?? '';
        const isRange = GAUGE_RANGE_ROLES.includes(role);
        const label = isRange
            // OMG's label says how to write it (mm², or AWG with "AWG"); add the crossover for what's typed
            ? `${field.label}${/awg/i.test(field.label) ? '' : ' - mm² (0.5) or AWG (20 AWG); a plain number is mm²'}${
                value.trim() ? ` · ${gaugeBoth(value, state.awg_to_mm2 || {}) || 'not a size OMG can read'}` : ''}`
            : `${field.label}${field.units ? ` (${field.units})` : ''}`;
        const required = profile?.required.includes(role);
        const set = (v: string | null) => setValues((old) => ({ ...old, [role]: v ?? '' }));
        const common = {
            label: field.template + (required ? ' *' : ''),
            description: label,
            error: errors[role] || (!field.exists ? `No '${field.template}' template in InvenTree yet` : undefined),
            disabled: !state.can_edit || !field.exists,
        };
        if (state.colour_roles.includes(role)) {
            const data = value && !state.colours.includes(value) ? [value, ...state.colours] : state.colours;
            return <Select key={role} {...common} data={data} value={value || null} onChange={set} searchable clearable />;
        }
        if (state.awg_roles.includes(role)) {
            return <Select key={role} {...common} data={state.standard_awg} value={value || null} onChange={set} searchable clearable />;
        }
        // Several values (an accessory's Connector Series, "DT, DTM") - pick
        // as many as fit from OMG's list; anything already typed stays shown.
        const listValues = field.choices.length ? field.choices : (field.suggestions || []);
        if (field.per_item && listValues.length && (state.accessory_types || []).includes(componentType)) {
            const picked = value.split(/[,;]/).map((v) => v.trim()).filter(Boolean);
            const data = [...picked.filter((v) => !listValues.includes(v)), ...listValues];
            return <MultiSelect key={role} {...common} data={data} value={picked} searchable clearable
                                onChange={(v) => set(v.join(', '))} />;
        }
        if (field.choices.length) {
            const data = value && !field.choices.includes(value) ? [value, ...field.choices] : field.choices;
            return <Select key={role} {...common} data={data} value={value || null} onChange={set} searchable clearable />;
        }
        if (field.suggestions?.length) {
            return <Autocomplete key={role} {...common} data={field.suggestions} value={value} onChange={set}
                                 placeholder="Pick from the list, or type" />;
        }
        return <TextInput key={role} {...common} value={value} onChange={(e) => set(e.currentTarget.value)} />;
    };

    return (
        <Stack gap="sm">
            <Group justify="space-between">
                <Title order={4}>OMG Part Setup</Title>
                <Button size="xs" variant="subtle" onClick={() => load(true)}>Reload from OMG</Button>
            </Group>
            <PartDataCheck context={context} partId={Number(partId)} />
            <Text size="sm" c="dimmed">
                Pick what this part is, then fill in what OMG needs for it. Gauge mm² and AWG fill each
                other in from OMG's AWG / mm² table; colours use OMG's colour list.
            </Text>
            <Stepper active={step ?? 0} size="sm" onStepClick={(i) => {
                if (i === 0 || typeSaved) setStep(i);
            }} allowNextStepsSelect={false}>
                <Stepper.Step label="What it is" description={typeSaved ? componentType : 'Component Type'}>
                    <Stack gap="sm" mt="sm">
                        <Select
                            label="Component Type"
                            data={Object.keys(state.profiles)}
                            value={componentType || null}
                            onChange={(v) => setComponentType(v ?? '')}
                            disabled={!state.can_edit || !state.fields['part.component_type']?.exists}
                            error={errors['part.component_type'] || (!state.fields['part.component_type']?.exists
                                ? `No '${state.fields['part.component_type']?.template}' template in InvenTree yet` : undefined)}
                            placeholder="Pick a type"
                        />
                        {state.component_type && !state.profile && (
                            <Alert color="yellow">Component Type is "{state.component_type}", which OMG has no setup for.</Alert>
                        )}
                        {state.profile && componentType && state.profile !== componentType && (
                            <Alert color="yellow">Saved as {state.profile} - saving changes it to {componentType}.</Alert>
                        )}
                        <Group>
                            <Button onClick={saveType} loading={saving} disabled={!state.can_edit || !componentType}>
                                {typeSaved ? 'Next' : 'Save type and continue'}</Button>
                        </Group>
                    </Stack>
                </Stepper.Step>

                <Stepper.Step label="Details" description="What OMG needs">
                    {profile && (
                        <Stack gap="sm" mt="sm">
                            {state.profile === componentType && state.missing_required.length > 0 && (
                                <Group gap="xs">
                                    <Text size="sm">Missing:</Text>
                                    {state.missing_required.map((role) => (
                                        <Badge key={role} color="red" variant="light">{state.fields[role]?.template || role}</Badge>
                                    ))}
                                </Group>
                            )}
                            {state.profile === componentType && state.missing_required.length === 0 && (
                                <Badge color="green" variant="light">Everything OMG needs is set</Badge>
                            )}
                            <Title order={6}>Required</Title>
                            {profile.required.map(fieldInput)}
                            {profile.optional.length > 0 && <Title order={6}>Optional</Title>}
                            {profile.optional.map(fieldInput)}
                            <Group>
                                <Button variant="default" onClick={() => setStep(0)}>Back</Button>
                                <Button onClick={save} loading={saving} disabled={!state.can_edit}>
                                    {componentType === 'Connector' ? 'Save and continue' : 'Save'}</Button>
                            </Group>
                        </Stack>
                    )}
                </Stepper.Step>

                {componentType === 'Connector' && (
                    <Stepper.Step label="Cavities & accessories" description="On their own panels">
                        <Stack gap="sm" mt="sm">
                            <Text size="sm" c="dimmed">
                                The connector's cavities (with its contacts, seals and blanks) and its locks, boots and
                                covers are set on their own panels, picked from the parts that fit.
                            </Text>
                            {['connector.cavity_map', 'connector.contact_count', 'connector.required_accessories',
                                'connector.preferred_accessories'].map(fieldInput)}
                            <Group>
                                <Button variant="default" onClick={() => setStep(1)}>Back</Button>
                                <Button onClick={() => openPanel('omg-cavities')}>Open OMG Cavities</Button>
                                <Button variant="light" onClick={() => openPanel('omg-accessories')}>Open OMG Accessories</Button>
                            </Group>
                        </Stack>
                    </Stepper.Step>
                )}
            </Stepper>
        </Stack>
    );
}

// ==================== Cavities ====================

interface PartInfo {
    pk: number;
    name: string;
    ipn: string;
    description: string;
    component_type: string;
    series: string;
    [key: string]: any;
}

interface CavityRow {
    cavities: string;
    series: string;
    sealing: string;
    max_od: string;
    problems?: string[];
}

interface CavityState {
    error: string | null;
    can_edit: boolean;
    part: { pk: number; name: string; ipn: string };
    map_template: string;
    map_template_exists: boolean;
    raw: string;
    rows: { cavities: string; series: string; solder: boolean; sealing: string; max_od: number | null; problems: string[] }[];
    related: PartInfo[];
    by_series: Record<string, PartInfo[]>;
    series_keys: Record<string, string>;    // series as written -> OMG's comparison key
    series_choices: string[];               // the Contact Series selection list
    solder_series: string;                  // OMG's series for solder-cup cavities (no contact, seal or blank)
    cavities: string[];                     // every cavity the saved map names
    sealings: string[];
    awg_to_mm2: Record<string, string | number>;
}

const TYPES = ['Contact', 'Seal', 'Blank'];

function OMGCavityPanel({ context }: { context: InvenTreePluginContext }) {
    const partId = context.id;
    const [state, setState] = useState<CavityState | null>(null);
    const [rows, setRows] = useState<CavityRow[]>([]);
    const [related, setRelated] = useState<Set<number>>(new Set());
    const [bySeries, setBySeries] = useState<Record<string, PartInfo[]>>({});
    // OMG decides how series compare ("DT-16" = "dt 16"); a series typed
    // but not looked up yet only matches itself exactly until "Find parts".
    const [seriesKeys, setSeriesKeys] = useState<Record<string, string>>({});
    const keyOf = useCallback((series: string) => seriesKeys[(series || '').trim()] ?? `raw:${(series || '').trim()}`,
        [seriesKeys]);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);

    const apply = useCallback((data: CavityState) => {
        setState(data);
        setRows((data.rows || []).map((r) => ({ cavities: r.cavities, series: r.series, sealing: r.sealing,
            max_od: r.max_od == null ? '' : String(r.max_od), problems: r.problems })));
        setRelated(new Set((data.related || []).map((p) => p.pk)));
        setBySeries(data.by_series || {});
        setSeriesKeys(data.series_keys || {});
    }, []);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const response = await context.api.get(`${PLUGIN}/cavity-setup/${partId}/`, LONG);
            apply(response.data);
        } catch (error: any) {
            setState(null);
            notifications.show({ color: 'red', title: 'OMG Cavities', message: apiError(error, "Couldn't load.") });
        } finally {
            setLoading(false);
        }
    }, [context.api, partId, apply]);

    useEffect(() => { load(); }, [load]);

    // Parts for series typed in the grid but not looked up yet
    const lookUp = useCallback(async () => {
        const solderName = (state?.solder_series || '').toLowerCase();
        const series = [...new Set(rows.map((r) => r.series.trim())
            .filter((v) => v && v.toLowerCase() !== solderName))];
        if (!series.length) return;
        try {
            const response = await context.api.get(`${PLUGIN}/cavity-setup/${partId}/`,
                { params: { series: series.join('|') }, ...LONG });
            setBySeries(response.data.by_series || {});
            setSeriesKeys((old) => ({ ...old, ...(response.data.series_keys || {}) }));
        } catch (error: any) {
            notifications.show({ color: 'red', title: 'OMG Cavities', message: apiError(error, 'Look-up failed.') });
        }
    }, [rows, state, context.api, partId]);

    const relatedParts = useMemo(() => {
        const all: Record<number, PartInfo> = {};
        for (const p of state?.related || []) all[p.pk] = p;
        for (const list of Object.values(bySeries)) for (const p of list) all[p.pk] = p;
        return all;
    }, [state, bySeries]);

    const preview = useMemo(() => rows
        .filter((r) => r.cavities.trim() && r.series.trim())
        .map((r) => [r.cavities.trim(), [r.series.trim(), r.sealing || 'none',
            ...(r.max_od.trim() ? [`maxod ${r.max_od.trim()}`] : [])].join(' / ')].join(': '))
        .join('; '), [rows]);

    const setRow = (i: number, patch: Partial<CavityRow>) =>
        setRows((old) => old.map((r, j) => (j === i ? { ...r, ...patch } : r)));

    const toggle = (pk: number, on: boolean) => setRelated((old) => {
        const next = new Set(old);
        if (on) next.add(pk); else next.delete(pk);
        return next;
    });

    const save = useCallback(async () => {
        if (!state) return;
        setSaving(true);
        const before = new Set((state.related || []).map((p) => p.pk));
        const related_add = [...related].filter((pk) => !before.has(pk));
        const related_remove = [...before].filter((pk) => !related.has(pk));
        try {
            const response = await context.api.post(`${PLUGIN}/cavity-setup/${partId}/`, {
                rows: rows.map((r) => ({ cavities: r.cavities, series: r.series, sealing: r.sealing, max_od: r.max_od })),
                related_add, related_remove,
            }, LONG);
            apply(response.data);
            notifications.show({ color: 'green', title: 'OMG Cavities', message: 'Saved.' });
        } catch (error: any) {
            notifications.show({ color: 'red', title: 'OMG Cavities', message: apiError(error, 'Save failed.') });
        } finally {
            setSaving(false);
        }
    }, [state, related, rows, context.api, partId, apply]);

    if (loading) return <Loader size="sm" />;
    if (!state) return <Alert color="red">Couldn't load the cavities.</Alert>;
    if (state.error) return <Alert color="orange" title="OMG Cavities">{state.error}</Alert>;

    // solder cups: the wire is soldered to the connector's pin - no parts to look up
    const solder = state.solder_series || '';
    const isSolder = (row: CavityRow) => !!solder && row.series.trim().toLowerCase() === solder.toLowerCase();
    const seriesChoices = [...(state.series_choices || []), ...(solder ? [solder] : [])];
    const seriesList = [...new Set(rows.filter((r) => !isSolder(r)).map((r) => r.series.trim()).filter(Boolean))];
    const relatedOfSeries = (series: string, type: string) => Object.values(relatedParts).filter((p) =>
        related.has(p.pk) && (p.series_key || `raw:${p.series}`) === keyOf(series)
        && (p.component_type || '').toLowerCase() === type.toLowerCase());

    return (
        <Stack gap="sm">
            <Title order={4}>OMG Cavities</Title>
            <Text size="sm" c="dimmed">
                Every cavity this connector has - as ranges (1-12, A-D) or single cavities (A, B, C, E) -
                with the contact series it takes, how it's sealed, and the largest wire insulation OD it
                accepts. This is the one list of cavities: OMG's pinout (empty cavities), blanks and
                Contact Count all come from it. OMG picks each wire end's contact, seal and every unused
                cavity's blank from the related parts of that series. Solder-cup cavities: pick the
                series "{state.solder_series}" - the wire is soldered to the connector's own pin, so no
                contact, seal or blank (set Strip Length and Min / Max Gauge on this connector in OMG Part
                Setup for the cups).
            </Text>
            {!state.map_template_exists && (
                <Alert color="red">No '{state.map_template}' parameter template in InvenTree yet - create it
                    (OMG &gt; InvenTree Settings &gt; Part Parameters can) before saving.</Alert>
            )}
            <Table withTableBorder>
                <Table.Thead>
                    <Table.Tr>
                        <Table.Th>Cavities</Table.Th><Table.Th>Series</Table.Th>
                        <Table.Th>Sealing</Table.Th><Table.Th>Max OD (mm)</Table.Th><Table.Th>Parts</Table.Th><Table.Th />
                    </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                    {rows.map((row, i) => {
                        const contacts = relatedOfSeries(row.series, 'Contact');
                        const seals = relatedOfSeries(row.series, 'Seal');
                        const blanks = relatedOfSeries(row.series, 'Blank');
                        return (
                            <Table.Tr key={i}>
                                <Table.Td><TextInput size="xs" value={row.cavities} placeholder="1-12, or A, B, C, E"
                                    onChange={(e) => setRow(i, { cavities: e.currentTarget.value })} /></Table.Td>
                                <Table.Td>{(state.series_choices || []).length
                                    // From the Contact Series list, so it always matches the contacts' own spelling
                                    ? <Select size="xs" searchable placeholder="Contact series"
                                        data={row.series && !seriesChoices.includes(row.series)
                                            ? [row.series, ...seriesChoices] : seriesChoices}
                                        value={row.series || null}
                                        onChange={(v) => setRow(i, { series: v || '',
                                            ...(v === solder && row.sealing === 'individual' ? { sealing: 'none' } : {}) })}
                                        error={row.series && !seriesChoices.includes(row.series)
                                            ? 'Not in the Contact Series list' : undefined} />
                                    : <TextInput size="xs" value={row.series} placeholder="e.g. DT-16"
                                        onChange={(e) => setRow(i, { series: e.currentTarget.value })} />}</Table.Td>
                                <Table.Td><Select size="xs" value={row.sealing || 'none'}
                                    data={isSolder(row) ? state.sealings.filter((v) => v !== 'individual') : state.sealings}
                                    onChange={(v) => setRow(i, { sealing: v ?? 'none' })} allowDeselect={false} /></Table.Td>
                                <Table.Td><TextInput size="xs" value={row.max_od} placeholder="optional"
                                    onChange={(e) => setRow(i, { max_od: e.currentTarget.value })} /></Table.Td>
                                <Table.Td>
                                    {isSolder(row) ? <Badge size="xs" color="gray" variant="light">solder cup - no parts</Badge> : <Group gap={4}>
                                        <Badge size="xs" color={contacts.length ? 'green' : 'red'}>{contacts.length} contact</Badge>
                                        {row.sealing === 'individual' && (
                                            <Badge size="xs" color={seals.length ? 'green' : 'red'}>{seals.length} seal</Badge>)}
                                        <Badge size="xs" color={blanks.length === 1 ? 'green' : 'orange'}>{blanks.length} blank</Badge>
                                    </Group>}
                                    {(row.problems || []).map((p, j) => <Text key={j} size="xs" c="red">{p}</Text>)}
                                </Table.Td>
                                <Table.Td><Button size="xs" color="red" variant="subtle"
                                    onClick={() => setRows((old) => old.filter((_r, j) => j !== i))}>Remove</Button></Table.Td>
                            </Table.Tr>
                        );
                    })}
                </Table.Tbody>
            </Table>
            <Group>
                <Button size="xs" variant="light"
                    onClick={() => setRows((old) => [...old, { cavities: '', series: '', sealing: 'none', max_od: '' }])}>
                    Add range</Button>
                <Button size="xs" variant="light" onClick={lookUp}>Find parts for these series</Button>
            </Group>
            <Text size="xs" c="dimmed">Cavity Map: {preview || '(empty)'}</Text>
            <Text size="xs" c="dimmed">Saved map: {(state.cavities || []).length} cavities
                {(state.cavities || []).length ? ` (${state.cavities.join(', ')})` : ''} - saving sets Contact
                Count to the number of cavities in the map.</Text>

            {seriesList.map((series) => {
                const parts = (bySeries[keyOf(series)] || []).filter((p) =>
                    TYPES.map((t) => t.toLowerCase()).includes((p.component_type || '').toLowerCase()));
                return (
                    <Stack key={series} gap={4}>
                        <Title order={6}>Series {series} - tick the parts this connector uses</Title>
                        {parts.length === 0 && <Text size="sm" c="dimmed">No InvenTree parts with Contact Series
                            "{series}" (and a Component Type of Contact, Seal or Blank) yet.</Text>}
                        {parts.length > 0 && (
                            <PartsTable context={context} parts={parts} picked={related} disabled={!state.can_edit}
                                onToggle={toggle} extraTitle="Type / fits"
                                extra={(p) => (
                                    <Stack gap={0}>
                                        <Badge size="xs" variant="light" color={
                                            p.component_type === 'Contact' ? 'blue' : p.component_type === 'Seal' ? 'cyan' : 'gray'}>
                                            {p.component_type}</Badge>
                                        {p['contact.min_gauge'] && <Text size="xs">
                                            {gaugeRange(p['contact.min_gauge'], p['contact.max_gauge'], state.awg_to_mm2 || {})}</Text>}
                                        {p['seal.min_od'] && <Text size="xs">OD {p['seal.min_od']} to {p['seal.max_od']} mm</Text>}
                                    </Stack>
                                )} />
                        )}
                    </Stack>
                );
            })}
            <Group>
                <Button onClick={save} loading={saving} disabled={!state.can_edit || !state.map_template_exists}>Save</Button>
            </Group>
        </Stack>
    );
}

// ==================== Accessories ====================

interface AccessoryPart {
    pk: number;
    name: string;
    ipn: string;
    description: string;
    thumbnail?: string;
    type: string;
    series: string;
    side: string;
    ways: string;
    exit: string;
}

interface KindRow {
    kind: string;
    required: boolean;
    quantity: number;
    status: string;
    default: number | null;
    options: AccessoryPart[];
    preferred: number[];
}

interface AccessoryState {
    error: string | null;
    can_edit: boolean;
    part: { pk: number; name: string; ipn: string };
    component_type: string;
    accessory_types: string[];
    diagram_only_types: string[];       // splice plugs - fitted from the wiring diagram
    missing_templates: string[];
    mode: 'connector' | 'accessory';
    // connector
    connector?: string;
    spec?: { series: string; side: string; ways: string; required: string; preferred: string };
    kinds?: KindRow[];
    problems?: string[];
    // accessory
    fits?: { series: string; side: string; ways: string } | null;
    connectors?: { pk: number; name: string; description: string; connector: string; required: boolean; preferred: boolean; thumbnail?: string }[];
}

interface EditRow {
    kind: string;
    required: boolean;
    quantity: string;
    preferred: string[];    // pks as text; none = no preference, two = both needed
    options: AccessoryPart[];
}

function OMGAccessoryPanel({ context }: { context: InvenTreePluginContext }) {
    const partId = context.id;
    const [state, setState] = useState<AccessoryState | null>(null);
    const [rows, setRows] = useState<EditRow[]>([]);
    const [linkRelated, setLinkRelated] = useState(true);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);

    const apply = useCallback((data: AccessoryState) => {
        setState(data);
        setRows((data.kinds || []).map((k) => ({
            kind: k.kind, required: k.required, quantity: String(k.quantity || 1),
            preferred: k.preferred.map(String),
            options: k.options,
        })));
    }, []);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const response = await context.api.get(`${PLUGIN}/accessory-setup/${partId}/`, LONG);
            apply(response.data);
        } catch (error: any) {
            setState(null);
            notifications.show({ color: 'red', title: 'OMG Accessories', message: apiError(error, "Couldn't load.") });
        } finally {
            setLoading(false);
        }
    }, [context.api, partId, apply]);

    useEffect(() => { load(); }, [load]);

    const save = useCallback(async () => {
        setSaving(true);
        try {
            const response = await context.api.post(`${PLUGIN}/accessory-setup/${partId}/`, {
                kinds: rows.map((r) => ({ kind: r.kind, required: r.required, quantity: r.quantity,
                    preferred_pks: r.preferred.map(Number) })),
                link_related: linkRelated,
            }, LONG);
            apply(response.data);
            notifications.show({ color: 'green', title: 'OMG Accessories', message: 'Saved.' });
        } catch (error: any) {
            notifications.show({ color: 'red', title: 'OMG Accessories', message: apiError(error, 'Save failed.') });
        } finally {
            setSaving(false);
        }
    }, [rows, linkRelated, context.api, partId, apply]);

    if (loading) return <Loader size="sm" />;
    if (!state) return <Alert color="red">Couldn't load the accessories.</Alert>;
    if (state.error) return <Alert color="orange" title="OMG Accessories">{state.error}</Alert>;

    const templatesAlert = state.missing_templates.length > 0 && (
        <Alert color="red">Missing parameter templates in InvenTree: {state.missing_templates.join(', ')} - create
            them (OMG &gt; InvenTree Settings &gt; Part Parameters can).</Alert>
    );

    if (state.mode === 'accessory') {
        const list = state.connectors || [];
        return (
            <Stack gap="sm">
                <Title order={4}>OMG Accessories - what this {state.component_type} fits</Title>
                {templatesAlert}
                <Text size="sm" c="dimmed">
                    Fits connectors with Connector Series "{state.fits?.series || '?'}", Side
                    "{state.fits?.side || 'Both'}" and {state.fits?.ways ? `${state.fits.ways} way` : 'any number of ways'}.
                    Change those in OMG Part Setup.
                </Text>
                {list.length === 0 && <Alert color="yellow">No connector in InvenTree matches yet - check its Connector
                    Series, Side and Ways, and the connectors' own Connector Series / Side / Contact Count.</Alert>}
                {list.length > 0 && (
                    <Table withTableBorder>
                        <Table.Thead><Table.Tr>
                            <Table.Th>Connector</Table.Th><Table.Th>Description</Table.Th><Table.Th />
                        </Table.Tr></Table.Thead>
                        <Table.Tbody>
                            {list.map((c) => (
                                <Table.Tr key={c.pk}>
                                    <Table.Td><Group gap="xs" wrap="nowrap">
                                        {c.thumbnail && <Image src={c.thumbnail} w={32} h={32} fit="contain" />}
                                        <PartNameLink context={context} part={c} panel="omg-accessories" />
                                    </Group></Table.Td>
                                    <Table.Td><Text size="sm">{c.description}</Text></Table.Td>
                                    <Table.Td><Group gap={4}>
                                        {c.required ? <Badge color="red" variant="light">required</Badge>
                                            : <Badge color="gray" variant="light">optional</Badge>}
                                        {c.preferred && <Badge color="green" variant="light">preferred</Badge>}
                                    </Group></Table.Td>
                                </Table.Tr>
                            ))}
                        </Table.Tbody>
                    </Table>
                )}
            </Stack>
        );
    }

    const setRow = (i: number, patch: Partial<EditRow>) =>
        setRows((old) => old.map((r, j) => (j === i ? { ...r, ...patch } : r)));
    // splice plugs: fitted where the wiring diagram has one, never required by the connector
    const unused = state.accessory_types.filter((t) => !rows.some((r) => r.kind === t)
        && !(state.diagram_only_types || []).includes(t));

    return (
        <Stack gap="sm">
            <Title order={4}>OMG Accessories{state.connector ? ` - ${state.connector}` : ''}</Title>
            {templatesAlert}
            <Text size="sm" c="dimmed">
                Locks, boots, covers... that fit this connector (same Connector Series and Side, and its Contact
                Count listed in their Ways). Tick the ones it must have - OMG adds them to every harness using this
                connector. Where several parts fit, pick the one to use (pick two if both are needed, e.g. signal and
                power locks), or leave it blank for whoever builds the harness to choose.
            </Text>
            {(state.problems || []).map((p, i) => <Alert key={i} color="yellow">{p}</Alert>)}
            {rows.length === 0 && <Text size="sm" c="dimmed">No accessory in InvenTree fits this connector yet.</Text>}
            {rows.length > 0 && (
                <Table withTableBorder>
                    <Table.Thead><Table.Tr>
                        <Table.Th>Kind</Table.Th><Table.Th>Required</Table.Th><Table.Th>Qty</Table.Th>
                        <Table.Th>Part to use</Table.Th>
                    </Table.Tr></Table.Thead>
                    <Table.Tbody>
                        {rows.map((r, i) => (
                            <Table.Tr key={r.kind}>
                                <Table.Td>{r.kind}</Table.Td>
                                <Table.Td><Checkbox checked={r.required} disabled={!state.can_edit}
                                    onChange={(e) => setRow(i, { required: e.currentTarget.checked })} /></Table.Td>
                                <Table.Td>{r.required && <TextInput size="xs" w={60} value={r.quantity}
                                    disabled={!state.can_edit}
                                    onChange={(e) => setRow(i, { quantity: e.currentTarget.value })} />}</Table.Td>
                                <Table.Td>
                                    {r.options.length === 0 && <Badge color="red" variant="light">nothing fits yet</Badge>}
                                    {r.options.length === 1 && (
                                        <Group gap="xs" wrap="nowrap">
                                            {r.options[0].thumbnail && <Image src={r.options[0].thumbnail} w={32} h={32} fit="contain" />}
                                            <Stack gap={0}>
                                                <PartNameLink context={context} part={r.options[0]} />
                                                <Text size="xs" c="dimmed">{r.options[0].description}</Text>
                                            </Stack>
                                        </Group>
                                    )}
                                    {r.options.length > 1 && (
                                        <Stack gap={2}>
                                            <Text size="xs" c="dimmed">
                                                {r.preferred.length === 0
                                                    ? (r.required ? 'Tick the one to use - none ticked = chosen when building the harness.'
                                                        : 'No preference - tick one to use it.')
                                                    : r.preferred.length > 1 ? `All ${r.preferred.length} ticked are needed (one of each).`
                                                        : 'This one is used.'}
                                            </Text>
                                            <PartsTable context={context} parts={r.options} disabled={!state.can_edit}
                                                picked={new Set(r.preferred.map(Number))}
                                                onToggle={(pk, on) => setRow(i, { preferred: on
                                                    ? [...r.preferred, String(pk)] : r.preferred.filter((x) => x !== String(pk)) })}
                                                extraTitle="Exit" extra={(p) => p.exit ? <Text size="xs">{p.exit}</Text> : null} />
                                        </Stack>
                                    )}
                                </Table.Td>
                            </Table.Tr>
                        ))}
                    </Table.Tbody>
                </Table>
            )}
            {unused.length > 0 && state.can_edit && (
                <Select size="xs" w={260} placeholder="Require another kind..." data={unused} value={null}
                    onChange={(v) => v && setRows((old) => [...old,
                        { kind: v, required: true, quantity: '1', preferred: [], options: [] }])} />
            )}
            <Checkbox checked={linkRelated} onChange={(e) => setLinkRelated(e.currentTarget.checked)}
                label="Also link the fitting parts as related parts (for reference in InvenTree)" />
            <Group>
                <Button onClick={save} loading={saving} disabled={!state.can_edit}>Save</Button>
                <Button variant="subtle" onClick={load}>Reload</Button>
            </Group>
        </Stack>
    );
}

export function RenderOMGAccessoryPanel(context: InvenTreePluginContext) {
    checkPluginVersion(context);
    return <OMGAccessoryPanel context={context} />;
}

export function RenderOMGPartSetupPanel(context: InvenTreePluginContext) {
    checkPluginVersion(context);
    return <OMGPartSetupPanel context={context} />;
}

export function RenderOMGCavityPanel(context: InvenTreePluginContext) {
    checkPluginVersion(context);
    return <OMGCavityPanel context={context} />;
}
