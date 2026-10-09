import { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, Badge, Button, Checkbox, Group, Loader, Select, Stack, Table, Text, TextInput, Title } from '@mantine/core';
import { notifications } from '@mantine/notifications';

import { checkPluginVersion, type InvenTreePluginContext } from '@inventreedb/ui';

// Two panels on a part's page (backend: part_setup.py):
//   OMG Part Setup - pick a Component Type, fill in every parameter it needs
//   OMG Cavities   - a connector's Cavity Map as a grid + its related
//                    contacts / seals / blanks by series
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
    from_cavity_groups: boolean;
    rows: { cavities: string; series: string; sealing: string; max_od: number | null; problems: string[] }[];
    related: PartInfo[];
    by_series: Record<string, PartInfo[]>;
    sealings: string[];
}

const normSeries = (s: string) => (s || '').replace(/[^0-9A-Za-z]+/g, '').toUpperCase();
const TYPES = ['Contact', 'Seal', 'Blank'];

function OMGCavityPanel({ context }: { context: InvenTreePluginContext }) {
    const partId = context.id;
    const [state, setState] = useState<CavityState | null>(null);
    const [rows, setRows] = useState<CavityRow[]>([]);
    const [related, setRelated] = useState<Set<number>>(new Set());
    const [bySeries, setBySeries] = useState<Record<string, PartInfo[]>>({});
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);

    const apply = useCallback((data: CavityState) => {
        setState(data);
        setRows((data.rows || []).map((r) => ({ cavities: r.cavities, series: r.series, sealing: r.sealing,
            max_od: r.max_od == null ? '' : String(r.max_od), problems: r.problems })));
        setRelated(new Set((data.related || []).map((p) => p.pk)));
        setBySeries(data.by_series || {});
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
        related.has(p.pk) && normSeries(p.series) === normSeries(series)
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
            {state.from_cavity_groups && (
                <Alert color="blue">Loaded from the older Cavity Groups parameter - saving writes a Cavity Map.</Alert>
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
                const parts = (bySeries[normSeries(series)] || []).filter((p) =>
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

export function RenderOMGPartSetupPanel(context: InvenTreePluginContext) {
    checkPluginVersion(context);
    return <OMGPartSetupPanel context={context} />;
}

export function RenderOMGCavityPanel(context: InvenTreePluginContext) {
    checkPluginVersion(context);
    return <OMGCavityPanel context={context} />;
}
