import { expect, test } from "@playwright/test";

import { API } from "./helpers";

// Round trip for the cook-file browser against a real backend.
//
// This suite drives ONE shared, stateful PiFire (playwright.config.ts sets
// workers: 1 for that reason) and is globally destructive to it. It therefore
// creates its own cook file by UPLOADING one rather than by running a cook: a
// real cook cycle would move the global grill mode and flush the history store
// out from under history.spec.ts, which seeds rows to get a chart to render.
//
// The fixture is built in-test, so the spec is self-contained, and every test
// deletes what it created in a `finally` -- a leftover cook file changes the
// listing another test asserts on.

// --------------------------------------------------------------------------
// A minimal-but-valid .pifire, built as a STORED (uncompressed) zip so no
// deflate implementation is needed. Members match what read_cookfile requires
// (file_mgmt/cookfile.py's json_types), and `version` comes from the running
// server so an upgrade is never triggered.
// --------------------------------------------------------------------------

function crc32(bytes: Uint8Array): number {
  let crc = ~0;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) {
      crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
    }
  }
  return ~crc >>> 0;
}

function storedZip(members: Record<string, string>): Buffer {
  const encoder = new TextEncoder();
  const locals: Buffer[] = [];
  const centrals: Buffer[] = [];
  let offset = 0;

  for (const [name, text] of Object.entries(members)) {
    const nameBytes = encoder.encode(name);
    const data = encoder.encode(text);
    const sum = crc32(data);

    const local = Buffer.alloc(30 + nameBytes.length + data.length);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4); // version needed
    local.writeUInt16LE(0, 6); // flags
    local.writeUInt16LE(0, 8); // method: stored
    local.writeUInt32LE(sum, 14);
    local.writeUInt32LE(data.length, 18);
    local.writeUInt32LE(data.length, 22);
    local.writeUInt16LE(nameBytes.length, 26);
    Buffer.from(nameBytes).copy(local, 30);
    Buffer.from(data).copy(local, 30 + nameBytes.length);
    locals.push(local);

    const central = Buffer.alloc(46 + nameBytes.length);
    central.writeUInt32LE(0x02014b50, 0);
    central.writeUInt16LE(20, 4);
    central.writeUInt16LE(20, 6);
    central.writeUInt16LE(0, 8);
    central.writeUInt16LE(0, 10);
    central.writeUInt32LE(sum, 16);
    central.writeUInt32LE(data.length, 20);
    central.writeUInt32LE(data.length, 24);
    central.writeUInt16LE(nameBytes.length, 28);
    central.writeUInt32LE(offset, 42);
    Buffer.from(nameBytes).copy(central, 46);
    centrals.push(central);

    offset += local.length;
  }

  const centralSize = centrals.reduce((total, buffer) => total + buffer.length, 0);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(centrals.length, 8);
  end.writeUInt16LE(centrals.length, 10);
  end.writeUInt32LE(centralSize, 12);
  end.writeUInt32LE(offset, 16);

  return Buffer.concat([...locals, ...centrals, end]);
}

function metric(overrides: Record<string, unknown>): Record<string, unknown> {
  return {
    id: 0,
    starttime: 0,
    starttime_c: "12:00:00",
    endtime: 0,
    endtime_c: "13:00:00",
    timeinmode: "1:00:00",
    mode: "Smoke",
    augerontime: 120,
    augerontime_c: "0:02:00",
    estusage_m: "300 grams",
    estusage_i: "0.66 pounds (10.58 ounces)",
    fanontime: 0,
    fanontime_c: "0:00:00",
    smokeplus: true,
    primary_setpoint: 225,
    smart_start_profile: 0,
    startup_temp: 0,
    p_mode: 2,
    auger_cycle_time: 20,
    pellet_level_start: 100,
    pellet_level_end: 95,
    pellet_brand_type: "Test Oak",
    ...overrides,
  };
}

function cookFileBytes(title: string, version: string): Buffer {
  const now = Date.now();
  const start = now - 3_600_000;
  return storedZip({
    "metadata.json": JSON.stringify({
      title,
      starttime: start,
      endtime: now,
      units: "F",
      thumbnail: "",
      id: `e2e-${title}`,
      version,
    }),
    "graph_data.json": JSON.stringify({
      time_labels: [start, now],
      chart_data: [
        {
          label: "Grill",
          borderColor: "rgb(0, 64, 255, 1)",
          hidden: false,
          data: [
            { x: start, y: 220 },
            { x: now, y: 240 },
          ],
        },
      ],
      probe_mapper: { probes: { grill1: 0 }, targets: {}, primarysp: {} },
    }),
    "raw_data.json": JSON.stringify([
      { T: start, P: { grill1: 220 }, PSP: 225, F: {}, NT: { grill1: 225 }, AUX: {} },
      { T: now, P: { grill1: 240 }, PSP: 225, F: {}, NT: { grill1: 225 }, AUX: {} },
    ]),
    "graph_labels.json": JSON.stringify({
      probes: { grill1: "Grill" },
      targets: {},
      primarysp: {},
    }),
    "events.json": JSON.stringify([
      metric({ id: 0, starttime: start, endtime: now }),
      metric({ id: 1, starttime: now, endtime: now, mode: "Stop", augerontime: 0 }),
    ]),
    "comments.json": "[]",
    "assets.json": "[]",
  });
}

/** The version the running server considers current. A fixture stamped with
 *  anything older makes read_cookfile answer 422 and the page offer a
 *  conversion, which is a different test. GET /api/settings answers
 *  {settings: {...}} -- not the {data, result, message} envelope the write
 *  endpoints use. */
async function serverCookfileVersion(
  request: import("@playwright/test").APIRequestContext,
): Promise<string> {
  const res = await request.get(`${API}/api/settings`);
  const body = (await res.json()) as { settings?: { versions?: { cookfile?: string } } };
  const version = body.settings?.versions?.cookfile;
  expect(version, "GET /api/settings did not report a cookfile version").toBeTruthy();
  return version as string;
}

async function uploadFixture(
  request: import("@playwright/test").APIRequestContext,
  name: string,
  bytes: Buffer,
): Promise<string> {
  const res = await request.post(`${API}/api/files/cookfiles/upload`, {
    multipart: { file: { name, mimeType: "application/octet-stream", buffer: bytes } },
  });
  expect(res.status()).toBe(200);
  const body = (await res.json()) as { data?: { filename?: string } };
  return body.data?.filename ?? name;
}

async function removeFixture(
  request: import("@playwright/test").APIRequestContext,
  name: string,
): Promise<void> {
  await request.post(`${API}/api/files/cookfiles/delete`, { data: { file: name } });
}

test.describe("cook file browser", () => {
  test("uploads, lists, opens, edits and deletes a cook file", async ({ page, request }) => {
    const version = await serverCookfileVersion(request);
    const name = await uploadFixture(
      request,
      "E2E-Roundtrip.pifire",
      cookFileBytes("E2E-Roundtrip", version),
    );

    try {
      await page.goto("/history");

      // Listed under Saved cooks, by title.
      const row = page.getByRole("link", { name: "E2E-Roundtrip", exact: true });
      await expect(row).toBeVisible();

      // Opens the detail route.
      await row.click();
      await expect(page).toHaveURL(new RegExp(`/cookfiles/${encodeURIComponent(name)}$`));
      await expect(page.getByRole("heading", { name: "E2E-Roundtrip", exact: true })).toBeVisible();

      // uPlot mounts a real <canvas>; the unit suite stubs the chart out
      // entirely, so this is the only place the actual plot is exercised.
      await expect(page.locator(".pf-history-chart canvas").first()).toBeVisible();

      // Events table, with the totals row the endpoint computes.
      await expect(page.getByText("Totals", { exact: true })).toBeVisible();

      // Rename the title, reload, and the new title survives.
      await page.getByLabel("Title").fill("E2E-Renamed");
      await page.getByRole("button", { name: "Save title", exact: true }).click();
      await page.reload();
      await expect(page.getByLabel("Title")).toHaveValue("E2E-Renamed");

      // Add a comment; it survives a reload.
      await page.getByLabel("Add a comment").fill("E2E note");
      await page.getByRole("button", { name: "Add comment", exact: true }).click();
      await expect(page.getByText("E2E note")).toBeVisible();
      await page.reload();
      await expect(page.getByText("E2E note")).toBeVisible();

      // Delete from /history and it leaves the list.
      await page.goto("/history");
      await page.getByRole("button", { name: `Delete ${name}`, exact: true }).click();
      await page.getByRole("button", { name: "Confirm", exact: true }).click();
      await expect(page.getByRole("link", { name: "E2E-Renamed", exact: true })).toHaveCount(0);
    } finally {
      await removeFixture(request, name);
    }
  });

  test("a corrupt archive offers Attempt Repair, not a blank page", async ({ page, request }) => {
    const name = await uploadFixture(
      request,
      "E2E-Corrupt.pifire",
      Buffer.from("this is not a zip file"),
    );

    try {
      await page.goto(`/cookfiles/${encodeURIComponent(name)}`);
      await expect(page.getByRole("button", { name: "Attempt Repair", exact: true })).toBeVisible();
      await expect(page.getByText("This cook file could not be loaded.")).toBeVisible();
    } finally {
      await removeFixture(request, name);
    }
  });

  test("the chart's CSV link downloads a CSV, not the SPA shell", async ({ page, request }) => {
    // Guards the /api/files placement: /history/export is NOT proxied by the
    // dev server, so a link there downloads index.html instead. "Download CSV
    // file" appears once under the graph card and "Download events CSV" twice
    // on the page, so each locator is exact and, where needed, scoped.
    const version = await serverCookfileVersion(request);
    const name = await uploadFixture(
      request,
      "E2E-Export.pifire",
      cookFileBytes("E2E-Export", version),
    );

    try {
      await page.goto(`/cookfiles/${encodeURIComponent(name)}`);
      const [download] = await Promise.all([
        page.waitForEvent("download"),
        page.getByRole("link", { name: "Download CSV file", exact: true }).click(),
      ]);
      expect(download.suggestedFilename()).toMatch(/\.csv$/);
    } finally {
      await removeFixture(request, name);
    }
  });
});
