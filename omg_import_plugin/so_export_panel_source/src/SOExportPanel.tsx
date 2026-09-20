import { useCallback, useState } from 'react';
import { Alert, Button, Stack, Text, Title } from '@mantine/core';

import { checkPluginVersion, type InvenTreePluginContext } from '@inventreedb/ui';

/**
 * "Parts List" panel — shown on a Sales Order's own detail page (see
 * core.py's get_ui_panels, target_model == 'salesorder'). One button,
 * downloads an XLSX of this order's line items (part number + qty) via
 * SalesOrderPartsListExportView.
 *
 * NOTE: whether InvenTree's panel system supports target_model ==
 * 'salesorder' the same way it's confirmed to support 'part' hasn't
 * been independently verified — it's inferred from a confirmed-working
 * PurchaseOrderDetail panel example in InvenTree's own plugin docs,
 * since PurchaseOrder/SalesOrder are sibling models. If this panel
 * doesn't actually appear on your Sales Order pages, that's the piece
 * to check first.
 */
function OMGSalesOrderPartsListPanel({ context }: { context: InvenTreePluginContext }) {
    const [downloading, setDownloading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const orderId = context.id;

    const downloadPartsList = useCallback(async () => {
        if (!orderId) return;
        setDownloading(true);
        setError(null);

        try {
            const response = await context.api.get(
                `/plugin/omg-harness-import/sales-order/${orderId}/parts-list-xlsx/`,
                { responseType: 'blob' },
            );
            const blob = new Blob([response.data], {
                type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            });
            const url = window.URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            // Filename comes from the server's Content-Disposition header in
            // a real download; this is just a reasonable fallback name for
            // the anchor element itself.
            link.download = `sales_order_${orderId}_parts_list.xlsx`;
            document.body.appendChild(link);
            link.click();
            link.remove();
            window.URL.revokeObjectURL(url);
        } catch (err: any) {
            const detail = err?.response?.data?.detail || err.message;
            setError(`Could not download the parts list: ${detail}`);
        } finally {
            setDownloading(false);
        }
    }, [orderId, context.api]);

    return (
        <Stack gap="sm">
            <Title order={4}>Parts List</Title>
            <Text size="sm" c="dimmed">
                Downloads an XLSX of this order's line items — part number
                and quantity, one row each.
            </Text>
            <Button onClick={downloadPartsList} loading={downloading} style={{ alignSelf: 'flex-start' }}>
                Download Parts List (XLSX)
            </Button>
            {error && <Alert color="red" title="Download issue">{error}</Alert>}
        </Stack>
    );
}

export function RenderOMGSalesOrderPartsListPanel(context: InvenTreePluginContext) {
    checkPluginVersion(context);
    return (
        <OMGSalesOrderPartsListPanel context={context} />
    );
}
