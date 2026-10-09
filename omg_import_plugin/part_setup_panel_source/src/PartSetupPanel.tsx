import { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, Badge, Button, Checkbox, Group, Loader, MultiSelect, Select, Stack, Table, Text, TextInput, Title } from '@mantine/core';
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
    colours: string[];
    colour_roles: string[];
    awg_roles: string[];
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
                { params: refresh ? { refresh: '1' } : {} });
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

    const save = useCallback(async () => {
        if (!state || !profile) return;
        setSaving(true);
        const roles = [...profile.required, ...profile.optional];
        const payload: Record<string, string> = {};
        for (const role of roles) payload[role] = values[role] ?? '';
        try {
            const response = await context.api.post(`${PLUGIN}/part-setup/${partId}/`,
                { component_type: componentType, values: payload });
            apply(response.data);
            notifications.show({ color: 'green', title: 'OMG Part Setup', message: 'Saved.' });
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

    const fieldInput = (role: string) => {
        const field = state.fields[role];
        if (!field) return null;
        const label = `${field.label}${field.units ? ` (${field.units})` : ''}`;
        const required = profile?.required.includes(role);
        const value = values[role] ?? '';
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
        if (field.choices.length) {
            return <Select key={role} {...common} data={field.choices} value={value || null} onChange={set} clearable />;
        }
        return <TextInput key={role} {...common} value={value} onChange={(e) => set(e.currentTarget.value)} />;
    };

    return (
        <Stack gap="sm">
            <Group justify="space-between">
                <Title order={4}>OMG Part Setup</Title>
                <Button size="xs" variant="subtle" onClick={() => load(true)}>Reload from OMG</Button>
            </Group>
            <Text size="sm" c="dimmed">
                Pick what this part is, then fill in what OMG needs for it. Gauge mm² and AWG fill each
                other in from OMG's AWG / mm² table; colours use OMG's colour list.
            </Text>
            <Select
                label="Component Type"
                data={Object.keys(state.profiles)}
                value={componentType || null}
                onChange={(v) => setComponentType(v ?? '')}
                disabled={!state.can_edit || !state.fields['part.component_type']?.exists}
                error={!state.fields['part.component_type']?.exists
                    ? `No '${state.fields['part.component_type']?.template}' template in InvenTree yet` : undefined}
                placeholder="Pick a type"
            />
            {state.component_type && !state.profile && (
                <Alert color="yellow">Component Type is "{state.component_type}", which OMG has no setup for.</Alert>
            )}
            {profile && (
                <>
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
                        <Button onClick={save} loading={saving} disabled={!state.can_edit}>Save</Button>
                    </Group>
                </>
            )}
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
    rows: { cavities: string; series: string; sealing: string; max_od: number | null; problems: string[] }[];
    related: PartInfo[];
    by_series: Record<string, PartInfo[]>;
    series_keys: Record<string, string>;    // series as written -> OMG's comparison key
    sealings: string[];
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
            const response = await context.api.get(`${PLUGIN}/cavity-setup/${partId}/`);
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
        const series = [...new Set(rows.map((r) => r.series.trim()).filter(Boolean))];
        if (!series.length) return;
        try {
            const response = await context.api.get(`${PLUGIN}/cavity-setup/${partId}/`,
                { params: { series: series.join('|') } });
            setBySeries(response.data.by_series || {});
            setSeriesKeys((old) => ({ ...old, ...(response.data.series_keys || {}) }));
        } catch (error: any) {
            notifications.show({ color: 'red', title: 'OMG Cavities', message: apiError(error, 'Look-up failed.') });
        }
    }, [rows, context.api, partId]);

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
            });
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

    const seriesList = [...new Set(rows.map((r) => r.series.trim()).filter(Boolean))];
    const relatedOfSeries = (series: string, type: string) => Object.values(relatedParts).filter((p) =>
        related.has(p.pk) && (p.series_key || `raw:${p.series}`) === keyOf(series)
        && (p.component_type || '').toLowerCase() === type.toLowerCase());

    return (
        <Stack gap="sm">
            <Title order={4}>OMG Cavities</Title>
            <Text size="sm" c="dimmed">
                Which contact series each range of cavities takes, how it's sealed, and the largest wire
                insulation OD it accepts. OMG picks each wire end's contact, seal and every unused
                cavity's blank from the related parts of that series.
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
                                <Table.Td><TextInput size="xs" value={row.cavities} placeholder="1-12, A-D"
                                    onChange={(e) => setRow(i, { cavities: e.currentTarget.value })} /></Table.Td>
                                <Table.Td><TextInput size="xs" value={row.series} placeholder="e.g. DT-16"
                                    onChange={(e) => setRow(i, { series: e.currentTarget.value })} /></Table.Td>
                                <Table.Td><Select size="xs" data={state.sealings} value={row.sealing || 'none'}
                                    onChange={(v) => setRow(i, { sealing: v ?? 'none' })} allowDeselect={false} /></Table.Td>
                                <Table.Td><TextInput size="xs" value={row.max_od} placeholder="optional"
                                    onChange={(e) => setRow(i, { max_od: e.currentTarget.value })} /></Table.Td>
                                <Table.Td>
                                    <Group gap={4}>
                                        <Badge size="xs" color={contacts.length ? 'green' : 'red'}>{contacts.length} contact</Badge>
                                        {row.sealing === 'individual' && (
                                            <Badge size="xs" color={seals.length ? 'green' : 'red'}>{seals.length} seal</Badge>)}
                                        <Badge size="xs" color={blanks.length === 1 ? 'green' : 'orange'}>{blanks.length} blank</Badge>
                                    </Group>
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

            {seriesList.map((series) => {
                const parts = (bySeries[keyOf(series)] || []).filter((p) =>
                    TYPES.map((t) => t.toLowerCase()).includes((p.component_type || '').toLowerCase()));
                return (
                    <Stack key={series} gap={4}>
                        <Title order={6}>Series {series} - tick the parts this connector uses</Title>
                        {parts.length === 0 && <Text size="sm" c="dimmed">No InvenTree parts with Contact Series
                            "{series}" (and a Component Type of Contact, Seal or Blank) yet.</Text>}
                        {parts.map((p) => (
                            <Checkbox key={p.pk} checked={related.has(p.pk)} disabled={!state.can_edit}
                                onChange={(e) => toggle(p.pk, e.currentTarget.checked)}
                                label={`${p.component_type}: ${p.name}${p.ipn ? ` (${p.ipn})` : ''}${
                                    p['contact.min_gauge'] ? ` - ${p['contact.min_gauge']} to ${p['contact.max_gauge']}` : ''}${
                                    p['seal.min_od'] ? ` - OD ${p['seal.min_od']} to ${p['seal.max_od']} mm` : ''}`} />
                        ))}
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
    missing_templates: string[];
    mode: 'connector' | 'accessory';
    // connector
    connector?: string;
    spec?: { series: string; side: string; ways: string; required: string; preferred: string };
    kinds?: KindRow[];
    problems?: string[];
    // accessory
    fits?: { series: string; side: string; ways: string } | null;
    connectors?: { pk: number; name: string; description: string; connector: string; required: boolean; preferred: boolean }[];
}

const accessoryLabel = (p: AccessoryPart) =>
    `${p.name}${p.exit ? ` [${p.exit} exit]` : ''}${p.description ? ` - ${p.description}` : ''}`;

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
            const response = await context.api.get(`${PLUGIN}/accessory-setup/${partId}/`);
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
            });
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
                                    <Table.Td><a href={`/web/part/${c.pk}/`}>{c.name}</a></Table.Td>
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
    const unused = state.accessory_types.filter((t) => !rows.some((r) => r.kind === t));

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
                                    {r.options.length === 1 && <Text size="sm">{accessoryLabel(r.options[0])}</Text>}
                                    {r.options.length > 1 && (
                                        <MultiSelect size="xs" disabled={!state.can_edit} clearable
                                            placeholder={r.preferred.length ? '' : (r.required ? 'Ask when building the harness' : 'No preference')}
                                            data={r.options.map((p) => ({ value: String(p.pk), label: accessoryLabel(p) }))}
                                            value={r.preferred} onChange={(v) => setRow(i, { preferred: v })} />
                                    )}
                                    {r.preferred.length > 1 && <Text size="xs" c="dimmed">
                                        All {r.preferred.length} are needed (one of each).</Text>}
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
