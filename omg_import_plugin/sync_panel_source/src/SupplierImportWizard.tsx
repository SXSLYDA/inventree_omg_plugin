/**
 * Import Supplier Part wizard - a copy of InvenTree's own ImportPartWizard
 * (src/frontend/src/components/wizards/ImportPartWizard.tsx + WizardDrawer.tsx,
 * InvenTree 1.1/1.2, MIT licence, (c) the InvenTree contributors), for use
 * from this plugin's review queue.
 *
 * InvenTree doesn't expose its wizard to plugins (not in @inventreedb/ui's
 * plugin context, not on window, no URL to open it), so this reproduces it:
 * same bottom drawer and step bar, same steps (Search Supplier Part ->
 * Category -> Parameters -> Stock -> Done), same endpoints and behaviour.
 * Differences, all because InvenTree's internals aren't available to plugins:
 *   - the category / parameter-template pickers are searchable Selects
 *     instead of InvenTree's StandaloneField
 *   - the Stock step lists stock with "Add stock" opening InvenTree's native
 *     stock form (context.forms.create) instead of its StockItemTable
 *   - "Edit part" uses InvenTree's native edit form (context.forms.edit)
 * Plus, for the review queue: a supplier and search term can be pre-set and
 * searched on open, and onImported() reports the new part to the caller.
 *
 * Parameters: InvenTree 1.2+ parameter/ ({model_type, model_id, template,
 * data}) - the old part/parameter/ endpoints of 1.1 are gone.
 */
import {
    ActionIcon,
    Badge,
    Box,
    Button,
    Card,
    Center,
    Checkbox,
    Divider,
    Drawer,
    Group,
    Loader,
    Paper,
    ScrollArea,
    Select,
    Stack,
    Stepper,
    Table,
    Text,
    TextInput,
    Title,
    Tooltip,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconArrowDown, IconCircleCheck, IconEdit, IconExternalLink, IconPlus, IconSearch } from '@tabler/icons-react';
import { type FormEvent, type ReactNode, useCallback, useEffect, useMemo, useState } from 'react';
import type { InvenTreePluginContext } from '@inventreedb/ui';

// Endpoint paths as literals: some of these enum entries don't exist in the
// @inventreedb/ui version that matches this server (0.7.0).
const EP = {
    supplierList: '/api/supplier/list/',
    supplierSearch: '/api/supplier/search/',
    supplierImport: '/api/supplier/import/',
    categoryList: '/api/part/category/',
    stockList: '/api/stock/',
    parameterList: '/api/parameter/',
    parameterTemplateList: '/api/parameter/template/',
};

const IMPORT_TIMEOUT_MS = 120000;

type SearchResultType = {
    id: string;
    sku: string;
    name: string;
    exact: boolean;
    description?: string;
    price?: string;
    link?: string;
    image_url?: string;
    existing_part_id?: number;
};

export type ImportResult = {
    manufacturer_part_id: number;
    supplier_part_id: number;
    part_id: number;
    pricing: { [priceBreak: number]: [number, string] };
    part_detail: any;
    parameters: {
        name: string;
        value: string;
        parameter_template: number | null;
        on_category: boolean;
    }[];
};

type SupplierChoice = { plugin_slug: string; supplier_slug: string; supplier_name: string };
type SelectedPart = { plugin: string; supplier: string; searchResult: SearchResultType };
type ParametersType = (ImportResult['parameters'][number] & { use: boolean })[];
type Option = { value: string; label: string };

const STEPS = ['Search Supplier Part', 'Category', 'Parameters', 'Stock', 'Done'];

// InvenTree's web UI base path (e.g. "/web"), taken from the current page
// (this panel lives on a part page: <base>/part/<pk>/...), for new-tab links.
function webBase(): string {
    const path = window.location.pathname;
    const i = path.indexOf('/part/');
    return i > 0 ? path.slice(0, i) : '/web';
}

function errorText(err: any): string {
    const data = err?.response?.data;
    if (typeof data?.detail === 'string') return data.detail;
    if (typeof data?.error === 'string') return data.error;
    if (data && typeof data === 'object') {
        return Object.entries(data).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(', ') : v}`).join('; ');
    }
    return err?.message || 'Unknown error';
}

// --- InvenTree's WizardProgressStepper (manual step changes disabled, as the import wizard sets) ---
function WizardProgressStepper({ currentStep, steps }: { currentStep: number; steps: string[] }) {
    const done = currentStep >= steps.length - 1;
    return (
        <Card p="xs" withBorder>
            <Group justify="center" gap="xs" wrap="nowrap">
                <Stepper active={currentStep} iconSize={20} size="xs">
                    {steps.map((step, idx) => (
                        <Stepper.Step label={step} key={step} aria-label={`wizard-step-${idx}`} allowStepSelect={false} />
                    ))}
                </Stepper>
                {done && (
                    <Tooltip label="Complete" position="top">
                        <ActionIcon color="green" variant="transparent"><IconCircleCheck /></ActionIcon>
                    </Tooltip>
                )}
            </Group>
        </Card>
    );
}

// --- InvenTree's SearchResult card ---
function SearchResultCard({ searchResult, rightSection, onOpenPart }: {
    searchResult: SearchResultType;
    rightSection?: ReactNode;
    onOpenPart: (pk: number) => void;
}) {
    return (
        <Paper withBorder p="md" shadow="xs">
            <Group justify="space-between" align="flex-start" gap="xs">
                {searchResult.image_url && (
                    <img src={searchResult.image_url} alt={searchResult.name} style={{ maxHeight: '50px' }} />
                )}
                <Stack gap={0} flex={1}>
                    <a href={searchResult.link} target="_blank" rel="noopener noreferrer">
                        <Text size="lg" w={500}>{searchResult.name} ({searchResult.sku})</Text>
                    </a>
                    <Text size="sm">{searchResult.description}</Text>
                </Stack>
                <Group gap="xs">
                    {searchResult.price && <Text size="sm" c="primary">{searchResult.price}</Text>}
                    {searchResult.exact && <Badge size="sm" color="green">Exact Match</Badge>}
                    {searchResult.existing_part_id && (
                        <Badge size="sm" color="blue" style={{ cursor: 'pointer' }}
                               onClick={() => onOpenPart(searchResult.existing_part_id as number)}>
                            Already Imported
                        </Badge>
                    )}
                    {rightSection}
                </Group>
            </Group>
        </Paper>
    );
}

// --- Searchable select that queries an InvenTree list endpoint (stands in for StandaloneField) ---
function ApiSelect({ context, url, params, value, onChange, label, labelOf, error, disabled, hideLabel }: {
    context: InvenTreePluginContext;
    url: string;
    params?: Record<string, any>;
    value: number | null;
    onChange: (value: number | null) => void;
    label?: string;
    labelOf: (row: any) => string;
    error?: string;
    disabled?: boolean;
    hideLabel?: boolean;
}) {
    const [search, setSearch] = useState('');
    const [options, setOptions] = useState<Option[]>([]);

    const load = useCallback(async (term: string) => {
        try {
            const response = await context.api.get(url, { params: { ...(params || {}), search: term || undefined, limit: 50 } });
            const rows = response.data?.results ?? response.data ?? [];
            setOptions((prev) => {
                const merged = new Map<string, Option>(prev.filter((o) => o.value === String(value)).map((o) => [o.value, o]));
                rows.forEach((r: any) => merged.set(String(r.pk), { value: String(r.pk), label: labelOf(r) }));
                return Array.from(merged.values());
            });
        } catch {
            /* leave options as they are */
        }
    }, [context.api, url, JSON.stringify(params), value]);

    // Make sure a pre-selected value (e.g. a matched parameter template) has its label.
    useEffect(() => {
        if (value === null || options.some((o) => o.value === String(value))) return;
        context.api.get(`${url}${value}/`)
            .then((r) => setOptions((prev) => [...prev, { value: String(value), label: labelOf(r.data) }]))
            .catch(() => {});
    }, [value]);

    useEffect(() => {
        const timer = setTimeout(() => load(search), 300);
        return () => clearTimeout(timer);
    }, [search]);

    return (
        <Select
            label={hideLabel ? undefined : label}
            aria-label={label}
            placeholder="Search..."
            searchable
            clearable
            searchValue={search}
            onSearchChange={setSearch}
            data={options}
            value={value === null ? null : String(value)}
            onChange={(v) => onChange(v ? Number(v) : null)}
            nothingFoundMessage="No results"
            filter={({ options: opts }) => opts}
            error={error}
            disabled={disabled}
        />
    );
}

// --- Step 1: InvenTree's SearchStep ---
function SearchStep({ context, initialTerm, preferredSupplier, selectSupplierPart }: {
    context: InvenTreePluginContext;
    initialTerm: string;
    preferredSupplier?: string;
    selectSupplierPart: (sp: SelectedPart) => void;
}) {
    const [searchValue, setSearchValue] = useState(initialTerm);
    const [supplier, setSupplier] = useState('');
    const [suppliers, setSuppliers] = useState<SupplierChoice[] | null>(null);
    const [supplierError, setSupplierError] = useState(false);
    const [searchResults, setSearchResults] = useState<SearchResultType[]>([]);
    const [isLoading, setIsLoading] = useState(false);
    const [searched, setSearched] = useState(false);
    const [searchError, setSearchError] = useState<string | null>(null);

    useEffect(() => {
        context.api.get(EP.supplierList)
            .then((r) => setSuppliers(r.data ?? []))
            .catch(() => { setSuppliers([]); setSupplierError(true); });
    }, [context.api]);

    // Pick the preferred supplier (e.g. Mouser) if offered, else the first - as InvenTree does.
    useEffect(() => {
        if (supplier !== '' || !suppliers || suppliers.length === 0) return;
        const preferred = suppliers.find((s) => s.supplier_slug === preferredSupplier) || suppliers[0];
        setSupplier(JSON.stringify([preferred.plugin_slug, preferred.supplier_slug]));
    }, [suppliers]);

    const runSearch = useCallback(async () => {
        if (!searchValue || !supplier) return;
        setIsLoading(true);
        setSearchError(null);
        const [plugin_slug, supplier_slug] = JSON.parse(supplier);
        try {
            const res = await context.api.get(EP.supplierSearch, {
                params: { plugin: plugin_slug, supplier: supplier_slug, term: searchValue },
                timeout: IMPORT_TIMEOUT_MS,
            });
            setSearchResults(res.data ?? []);
        } catch (err: any) {
            setSearchResults([]);
            setSearchError(errorText(err));
        }
        setSearched(true);
        setIsLoading(false);
    }, [context.api, supplier, searchValue]);

    // Search straight away when opened with a term (the review queue's MPN).
    useEffect(() => {
        if (supplier && initialTerm && !searched) runSearch();
    }, [supplier]);

    const handleSearch = (e: FormEvent<HTMLFormElement>) => { e.preventDefault(); runSearch(); };

    return (
        <Stack>
            <form onSubmit={handleSearch}>
                <Group align="flex-end">
                    <TextInput aria-label="textbox-search-for-part" flex={1} placeholder="Search for a part"
                               label="Search" value={searchValue}
                               onChange={(event) => setSearchValue(event.currentTarget.value)} />
                    <Select
                        label="Supplier"
                        value={supplier}
                        onChange={(value) => setSupplier(value ?? '')}
                        data={(suppliers || []).map((s) => ({
                            value: JSON.stringify([s.plugin_slug, s.supplier_slug]),
                            label: s.supplier_name,
                        }))}
                        searchable
                        disabled={suppliers === null || supplierError}
                        placeholder={suppliers === null ? 'Loading...' : supplierError ? 'Error fetching suppliers' : 'Select supplier'}
                    />
                    <Button color="blue" disabled={!searchValue || !supplier} type="submit" leftSection={<IconSearch />}>
                        Search
                    </Button>
                </Group>
            </form>

            {isLoading && <Center><Loader /></Center>}
            {!isLoading && searchError && <Text size="sm" c="red">{searchError}</Text>}
            {!isLoading && !searchError && <Text size="sm" c="dimmed">Found {searchResults.length} results</Text>}

            <ScrollArea.Autosize mah="49vh">
                <Stack gap="xs">
                    {searchResults.map((res, index) => (
                        <SearchResultCard
                            key={`${res.id}-${index}`}
                            searchResult={res}
                            onOpenPart={(pk) => context.navigate(`/part/${pk}/`)}
                            rightSection={!res.existing_part_id && (
                                <Tooltip label="Import this part">
                                    <ActionIcon aria-label={`action-button-import-part-${res.id}`}
                                                onClick={() => {
                                                    const [plugin_slug, supplier_slug] = JSON.parse(supplier);
                                                    selectSupplierPart({ plugin: plugin_slug, supplier: supplier_slug, searchResult: res });
                                                }}>
                                        <IconArrowDown size={18} />
                                    </ActionIcon>
                                </Tooltip>
                            )}
                        />
                    ))}
                </Stack>
            </ScrollArea.Autosize>
        </Stack>
    );
}

// --- Step 2: InvenTree's CategoryStep ---
function CategoryStep({ context, isImporting, importPart }: {
    context: InvenTreePluginContext;
    isImporting: boolean;
    importPart: (categoryId: number) => void;
}) {
    const [category, setCategory] = useState<number | null>(null);
    return (
        <Stack>
            <ApiSelect context={context} url={EP.categoryList} params={{ structural: false }}
                       label="Select category" value={category} onChange={setCategory}
                       labelOf={(c) => c.pathstring || c.name} />
            <Text>Are you sure you want to import this part into the selected category now?</Text>
            <Group justify="flex-end">
                <Button aria-label="action-button-import-part-now" disabled={!category || isImporting}
                        onClick={() => importPart(category as number)} loading={isImporting}>
                    Import Now
                </Button>
            </Group>
        </Stack>
    );
}

// --- Step 3: InvenTree's ParametersStep ---
function ParametersStep({ context, importResult, isImporting, skipStep, importParameters, parameterErrors, templateUrl }: {
    context: InvenTreePluginContext;
    importResult: ImportResult;
    isImporting: boolean;
    skipStep: () => void;
    importParameters: (parameters: ParametersType) => Promise<void>;
    parameterErrors: { template?: string; data?: string }[] | null;
    templateUrl: string;
}) {
    const [parameters, setParameters] = useState<ParametersType>(() =>
        (importResult.parameters || []).map((p) => ({ ...p, use: p.parameter_template !== null })));
    const [categoryCount, otherCount] = useMemo(() => [
        parameters.filter((x) => x.on_category && x.use).length,
        parameters.filter((x) => !x.on_category && x.use).length,
    ], [parameters]);
    const parametersFromCategory = useMemo(() => parameters.filter((x) => x.on_category).length, [parameters]);
    const setParameter = useCallback((i: number, key: string) => (e: unknown) =>
        setParameters((p) => p.map((row, j) => (i === j ? { ...row, [key]: e } : row))), []);

    return (
        <Stack>
            <Text size="sm">Select and edit the parameters you want to add to this part.</Text>
            {parametersFromCategory > 0 && (
                <Title order={5}>Default category parameters<Badge ml="xs">{categoryCount}</Badge></Title>
            )}
            <Stack gap="xs">
                {parameters.map((p, i) => (
                    <Stack key={i}>
                        {p.on_category === false && parameters[i - 1]?.on_category === true && (
                            <>
                                <Divider />
                                <Title order={5}>Other parameters<Badge ml="xs">{otherCount}</Badge></Title>
                            </>
                        )}
                        <Group align="center" gap="xs">
                            <Checkbox checked={p.use} onChange={(e) => setParameter(i, 'use')(e.currentTarget.checked)} />
                            {!p.on_category && (
                                <Tooltip label={p.name}>
                                    <Text w="160px" style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                                        {p.name}
                                    </Text>
                                </Tooltip>
                            )}
                            <Box flex={1}>
                                <ApiSelect context={context} url={templateUrl} label="Parameter template" hideLabel
                                           value={p.parameter_template} disabled={p.on_category}
                                           labelOf={(t) => (t.units ? `${t.name} [${t.units}]` : t.name)}
                                           error={parameterErrors?.[i]?.template}
                                           onChange={(v) => {
                                               if (!p.parameter_template) setParameter(i, 'use')(true);
                                               setParameter(i, 'parameter_template')(v);
                                           }} />
                            </Box>
                            <TextInput flex={1} value={p.value}
                                       onChange={(e) => setParameter(i, 'value')(e.currentTarget.value)}
                                       error={parameterErrors?.[i]?.data} />
                        </Group>
                    </Stack>
                ))}
                <Tooltip label="Add a new parameter">
                    <ActionIcon onClick={() => setParameters((p) => [...p, {
                        name: '', value: '', parameter_template: null, on_category: false, use: true,
                    }])}>
                        <IconPlus size={18} />
                    </ActionIcon>
                </Tooltip>
            </Stack>
            <Group justify="flex-end">
                <Button onClick={skipStep}>Skip</Button>
                <Button aria-label="action-button-import-create-parameters"
                        disabled={isImporting || parameters.filter((p) => p.use).length === 0}
                        loading={isImporting} onClick={() => importParameters(parameters)}>
                    Create Parameters
                </Button>
            </Group>
        </Stack>
    );
}

// --- Step 4: InvenTree's StockStep (native "Add stock" form + a simple list) ---
function StockStep({ context, importResult, nextStep }: {
    context: InvenTreePluginContext;
    importResult: ImportResult;
    nextStep: () => void;
}) {
    const [items, setItems] = useState<any[]>([]);
    const load = useCallback(async () => {
        try {
            const r = await context.api.get(EP.stockList, {
                params: { part: importResult.part_id, supplier_part: importResult.supplier_part_id, location_detail: true },
            });
            setItems(r.data?.results ?? r.data ?? []);
        } catch {
            setItems([]);
        }
    }, [context.api, importResult]);
    useEffect(() => { load(); }, [load]);

    // First price break as the suggested purchase price, as InvenTree's table does.
    const firstPrice = useMemo(() => {
        const breaks = Object.keys(importResult.pricing || {}).map(Number).sort((a, b) => a - b);
        return breaks.length ? importResult.pricing[breaks[0]] : null;
    }, [importResult]);

    const addStock = context.forms.create({
        url: 'stock/',
        title: 'Add Stock Item',
        fields: {
            part: { hidden: true },
            supplier_part: { hidden: true },
            quantity: {},
            location: {},
            batch: {},
            purchase_price: {},
            purchase_price_currency: {},
            status: {},
            notes: {},
        },
        initialData: {
            part: importResult.part_id,
            supplier_part: importResult.supplier_part_id,
            ...(firstPrice ? { purchase_price: firstPrice[0], purchase_price_currency: firstPrice[1] } : {}),
        },
        follow: false,
        onFormSuccess: () => load(),
    });

    return (
        <Stack>
            <Text size="sm">Create initial stock for the imported part.</Text>
            {addStock.modal}
            <Group>
                <Button leftSection={<IconPlus size={16} />} variant="light" onClick={() => addStock.open()}>
                    Add stock
                </Button>
            </Group>
            {items.length > 0 ? (
                <Table withTableBorder striped>
                    <Table.Thead>
                        <Table.Tr><Table.Th>Quantity</Table.Th><Table.Th>Location</Table.Th><Table.Th>Batch</Table.Th></Table.Tr>
                    </Table.Thead>
                    <Table.Tbody>
                        {items.map((s) => (
                            <Table.Tr key={s.pk}>
                                <Table.Td>{s.quantity}</Table.Td>
                                <Table.Td>{s.location_detail?.pathstring || s.location_detail?.name || '—'}</Table.Td>
                                <Table.Td>{s.batch || '—'}</Table.Td>
                            </Table.Tr>
                        ))}
                    </Table.Tbody>
                </Table>
            ) : (
                <Text size="sm" c="dimmed">No stock yet.</Text>
            )}
            <Group justify="flex-end">
                <Button onClick={nextStep} aria-label="action-button-import-stock-next">Next</Button>
            </Group>
        </Stack>
    );
}

// --- The wizard (InvenTree's ImportPartWizard + WizardDrawer) ---
export default function SupplierImportWizard({ context, opened, onClose, initialTerm = '', preferredSupplier, onImported, title = 'Import Supplier Part' }: {
    context: InvenTreePluginContext;
    opened: boolean;
    onClose: () => void;
    initialTerm?: string;
    preferredSupplier?: string;
    onImported?: (result: ImportResult, selected: SelectedPart) => void;
    title?: string;
}) {
    const [step, setStep] = useState(0);
    const [supplierPart, setSupplierPart] = useState<SelectedPart>();
    const [importResult, setImportResult] = useState<ImportResult>();
    const [isImporting, setIsImporting] = useState(false);
    const [parameterErrors, setParameterErrors] = useState<{ template?: string; data?: string }[] | null>(null);

    const editPart = context.forms.edit({
        url: 'part/',
        pk: importResult?.part_id,
        title: 'Edit Part',
        fields: { name: {}, IPN: {}, description: {}, category: {}, keywords: {}, link: {}, units: {} },
    });

    const reset = useCallback(() => {
        setStep(0);
        setSupplierPart(undefined);
        setImportResult(undefined);
        setIsImporting(false);
        setParameterErrors(null);
    }, []);

    const close = useCallback(() => { reset(); onClose(); }, [reset, onClose]);

    const importPart = useCallback(async (categoryId: number) => {
        setIsImporting(true);
        try {
            const response = await context.api.post(EP.supplierImport, {
                category_id: categoryId,
                part_import_id: supplierPart?.searchResult.id,
                plugin: supplierPart?.plugin,
                supplier: supplierPart?.supplier,
            }, { timeout: IMPORT_TIMEOUT_MS });
            setImportResult(response.data);
            notifications.show({ title: 'Success', message: 'Part imported successfully!', color: 'green' });
            if (supplierPart) onImported?.(response.data, supplierPart);
            setStep(2);
        } catch (err: any) {
            notifications.show({ title: 'Error', message: `Failed to import part: ${errorText(err)}`, color: 'red' });
        }
        setIsImporting(false);
    }, [context.api, supplierPart, onImported]);

    const importParameters = useCallback(async (parameters: ParametersType) => {
        if (!importResult) return;
        setIsImporting(true);
        setParameterErrors(null);
        const useParameters = parameters.map((x, i) => ({ ...x, i })).filter((p) => p.use);
        const map = useParameters.reduce((acc, p, i) => { acc[p.i] = i; return acc; }, {} as Record<number, number>);
        const payload = useParameters.map((p) => (
            { model_type: 'part', model_id: importResult.part_id, template: p.parameter_template, data: p.value }));
        try {
            await context.api.post(EP.parameterList, payload);
            notifications.show({ title: 'Success', message: 'Parameters created successfully!', color: 'green' });
            setStep(3);
        } catch (err: any) {
            if (err?.response?.status === 400 && Array.isArray(err.response.data)) {
                const errors = err.response.data.map((e: Record<string, string[]>) => ({
                    ...(e.data ? { data: e.data.join(',') } : {}),
                    ...(e.template ? { template: e.template.join(',') } : {}),
                }));
                setParameterErrors(parameters.map((_, i) => (map[i] !== undefined && errors[map[i]] ? errors[map[i]] : {})));
            }
            notifications.show({ title: 'Error', message: 'Failed to create parameters, please fix the errors and try again', color: 'red' });
        }
        setIsImporting(false);
    }, [context.api, importResult]);

    const openPage = (path: string) => { close(); context.navigate(path); };

    return (
        <Drawer
            position="bottom"
            size="75%"
            opened={opened}
            onClose={close}
            withCloseButton
            closeOnEscape={false}
            closeOnClickOutside={false}
            styles={{ header: { width: '100%' }, title: { width: '100%' } }}
            title={
                <Stack gap="xs" style={{ width: '100%' }}>
                    <Group gap="xs" wrap="nowrap" justify="space-between" grow preventGrowOverflow={false}>
                        <Text size="xl" fw={700}>{title}</Text>
                        <WizardProgressStepper currentStep={step} steps={STEPS} />
                        <span />
                    </Group>
                    <Divider />
                </Stack>
            }
        >
            <Stack gap="xs">
                {editPart.modal}

                {step > 0 && supplierPart && (
                    <SearchResultCard
                        searchResult={supplierPart.searchResult}
                        onOpenPart={(pk) => openPage(`/part/${pk}/`)}
                        rightSection={importResult && (
                            <Group gap="xs">
                                <Tooltip label="Open part">
                                    <ActionIcon variant="subtle" onClick={() => window.open(`${webBase()}/part/${importResult.part_id}/`, '_blank')}>
                                        <IconExternalLink size={18} />
                                    </ActionIcon>
                                </Tooltip>
                                <Tooltip label="Edit part">
                                    <ActionIcon variant="subtle" onClick={() => editPart.open()}><IconEdit size={18} /></ActionIcon>
                                </Tooltip>
                            </Group>
                        )}
                    />
                )}

                {step === 0 && opened && (
                    <SearchStep context={context} initialTerm={initialTerm} preferredSupplier={preferredSupplier}
                                selectSupplierPart={(sp) => { setSupplierPart(sp); setStep(1); }} />
                )}

                {step === 1 && (
                    <CategoryStep context={context} isImporting={isImporting} importPart={importPart} />
                )}

                {step === 2 && importResult && (
                    <ParametersStep context={context} importResult={importResult} isImporting={isImporting}
                                    parameterErrors={parameterErrors} importParameters={importParameters}
                                    templateUrl={EP.parameterTemplateList}
                                    skipStep={() => setStep(3)} />
                )}

                {step === 3 && importResult && (
                    <StockStep context={context} importResult={importResult} nextStep={() => setStep(4)} />
                )}

                {step === 4 && importResult && (
                    <Stack>
                        <Text size="sm">Part imported successfully from supplier {supplierPart?.supplier}.</Text>
                        <Group justify="flex-end">
                            <Button variant="light" aria-label="action-button-import-open-part"
                                    onClick={() => openPage(`/part/${importResult.part_id}/`)}>Open Part</Button>
                            <Button variant="light"
                                    onClick={() => openPage(`/purchasing/supplier-part/${importResult.supplier_part_id}/`)}>Open Supplier Part</Button>
                            <Button variant="light"
                                    onClick={() => openPage(`/purchasing/manufacturer-part/${importResult.manufacturer_part_id}/`)}>Open Manufacturer Part</Button>
                            <Button onClick={close} aria-label="action-button-import-close">Close</Button>
                        </Group>
                    </Stack>
                )}
            </Stack>
        </Drawer>
    );
}
