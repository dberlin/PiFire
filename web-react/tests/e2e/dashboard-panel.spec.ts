import { mkdirSync } from "node:fs";
import type {
  ModelEvidenceReport,
  ModelEvidenceStatus,
  PidSpLearningReport,
} from "@pifire/core/contracts/learning";
import { expect, test } from "@playwright/test";
import { freezeDate } from "./layoutBaseline";

// 800x480 -- the grill's own screen.
//
// The reflow shipped with three width bands and gates on two of them:
// dashboard-fidelity.spec.ts holds 1280x720 still, dashboard-reflow.spec.ts
// drives 390x844. The band in between, `@media (max-width: 1279px)`, had no
// coverage at all -- and it is the only one an actual PiFire device renders.
//
// It was broken. Measured at 800x480: the three columns never wrapped, because
// the centre column's desktop `flex: 1` is a flex-basis of 0 and it lost every
// negotiation against two siblings asking for 260px each. probeCol took 331px,
// rightCol took 331px, and the column carrying the gauge, the cook row and all
// five control buttons was left 71px
// -- a 320px gauge rendered into a 69px svg, and 91px-wide buttons overhanging
// a 71px container.
//
// Same demo server as the other two projects, for the same reason: no socket,
// so this cannot be raced by the shared PiFire instance the rest of the suite
// mutates, and demoDashAt pins the content.

const ARTIFACTS = "tests/e2e/artifacts";

const EXACT_DIGEST = "b".repeat(64);
const EXACT_DECISION = "review-exact";

const fixtureDigest = (value: string) =>
  Array.from(value, (character) => character.charCodeAt(0).toString(16))
    .join("")
    .padEnd(64, "0")
    .slice(0, 64);

function evidenceReport(
  status: ModelEvidenceStatus,
  digest: string,
  decisionId: string,
  options: {
    origin?: "passive-online" | "operator-calibration";
    roleGeneration?: number;
  } = {},
): ModelEvidenceReport {
  const origin = options.origin ?? "passive-online";
  const policy = "causal-auto" as const;
  const roleGeneration = options.roleGeneration ?? 4;
  const complete = !["warming", "collecting", "fitting"].includes(status);
  const activationPhase =
    status === "active" ? "active" : status === "activating" ? "prepared" : "aborted";
  const challengerPhase =
    status === "activating"
      ? "activating"
      : ["qualified", "active"].includes(status)
        ? "qualified"
        : "evaluating";
  const activeDigest = "c".repeat(64);
  const corpusDigest = "e".repeat(64);
  const requiredHorizons: (3 | 15 | 45 | 90 | 180)[] = [3, 15, 45, 90, 180];
  const qualified = ["qualified", "activating", "active"].includes(status);
  return {
    schema_version: 3,
    status,
    mode: origin,
    decision_id: decisionId,
    evidence: {
      count: complete ? 6 : 0,
      audit_count: complete ? 7 : 0,
      high_water: complete ? [1_780_000_601_000, "evidence-e2e"] : null,
      retired_excluded: complete ? 1 : 0,
    },
    fit: {
      status:
        status === "fitting"
          ? "running"
          : status === "warming" || status === "collecting"
            ? "idle"
            : "succeeded",
      request_id: status === "warming" || status === "collecting" ? null : "fit-e2e-5",
      fit_corpus_digest: status === "warming" || status === "collecting" ? null : corpusDigest,
      error: null,
    },
    checks: complete
      ? {
          identifiability: "passed",
          native_build: "passed",
          native_dry_solve: "passed",
          target_timing: "passed",
        }
      : {},
    candidate: complete
      ? {
          challenger_id: "challenger-e2e-5",
          phase: challengerPhase,
          digest,
          origin,
          policy,
          role_generation: roleGeneration,
          candidate_generation: 5,
          parameters: {
            C_c: 4475,
            h_amb: 18.5,
            T_amb: 20,
            theta: 150,
            n_delay: 8,
            K_Q: 0.076,
            sigma: 0,
          },
          parameter_deltas: null,
          fit_quality: 0.5,
          identifiability: null,
          assessment: {
            decision_id: decisionId,
            origin,
            policy,
            fit_accepted: true,
            identifiability_accepted: true,
            native_build: "passed",
            native_dry_solve: "passed",
            target_timing: "passed",
            confidence_accepted: true,
            rejection_reasons: [],
            payload_type: "candidate_assessment",
          },
          lineage: {
            request_id: "fit-e2e-5",
            parent_incumbent_digest: activeDigest,
            parent_incumbent_generation: roleGeneration,
            candidate_generation: 5,
            fit_corpus_digest: corpusDigest,
            trigger_origin: origin,
            result_status: "succeeded",
            candidate_digest: digest,
          },
        }
      : null,
    evaluation: complete
      ? {
          epoch: status === "interrupted" ? 2 : 1,
          round: qualified ? 2 : 1,
          completed_horizons: qualified ? requiredHorizons : [3, 15],
          required_horizons: requiredHorizons,
          wins: qualified ? 2 : 1,
          required_wins: 2,
          resumed_from_previous_cook: status === "interrupted",
          pending_origins:
            status === "evaluating"
              ? [
                  {
                    origin_sequence: 121,
                    horizon_steps: 45,
                    role_generation: roleGeneration,
                    candidate_generation: 5,
                    incumbent_digest: activeDigest,
                    candidate_digest: digest,
                  },
                ]
              : [],
        }
      : null,
    corpus: {
      digest: corpusDigest,
      revision: 7,
      fit_partition_digest: "f".repeat(64),
      slices: [
        {
          segment_id: "segment-e2e-5",
          through_ordinal: 120,
          prefix_digest: "a".repeat(64),
          segment_content_digest: "d".repeat(64),
          pre_roll_count: 20,
          scored_count: 101,
        },
      ],
    },
    activation: {
      phase: activationPhase,
      transaction_id: complete ? "transaction-e2e-5" : null,
      origin,
      policy,
      candidate_digest: digest,
      candidate_generation: 5,
      role_generation: roleGeneration,
      reason: null,
      pending_persistence: false,
      pending_frame_boundary_swap: status === "activating",
    },
    active_model: {
      digest: status === "active" ? digest : activeDigest,
      role_generation: roleGeneration,
    },
    identities: {
      active_digest: status === "active" ? digest : activeDigest,
      active_generation: roleGeneration,
      candidate_digest: digest,
      candidate_generation: 5,
      rollback_digest: status === "active" ? activeDigest : null,
      rollback_generation: status === "active" ? roleGeneration - 1 : null,
    },
    calibration: {
      revision: complete ? 2 : 0,
      command_high_water: complete ? 2 : 0,
    },
    latest_lifecycle: complete
      ? {
          decision_id: decisionId,
          phase: activationPhase,
          origin,
          policy,
          reason: null,
          payload_type: "activation_lifecycle",
        }
      : null,
    failure: null,
    gates: complete
      ? [
          { name: "native_build", passed: true, reason: null },
          { name: "native_dry_solve", passed: true, reason: null },
          { name: "target_timing", passed: true, reason: null },
        ]
      : [],
    blockers: [],
    errors: [],
    revision: fixtureDigest(`${status}-${roleGeneration}`),
  };
}

function pidSpReport(): PidSpLearningReport {
  const trusted = {
    form: "ipdt" as const,
    K_i: 0.043,
    c0: -0.006,
    theta: 18,
  };
  const predictorModel = {
    form: "ipdt" as const,
    K_i: 0.043,
    c0: -0.006,
    theta: 18,
  };
  return {
    schema_version: 1,
    controller: "pid_sp",
    status: "active",
    live: true,
    revision: "a".repeat(64),
    gates: [
      {
        name: "accepted_samples",
        passed: true,
        observed: 480,
        required: 360,
        unit: null,
      },
      {
        name: "accepted_duration",
        passed: true,
        observed: 960,
        required: 900,
        unit: "seconds",
      },
      {
        name: "duty_standard_deviation",
        passed: true,
        observed: 0.19,
        required: 0.15,
        unit: null,
      },
      {
        name: "duty_transition",
        passed: true,
        observed: true,
        required: true,
        unit: null,
      },
      {
        name: "temperature_span",
        passed: true,
        observed: 23.5,
        required: 18,
        unit: "°F",
      },
    ],
    confirmation: {
      observed: 3,
      required: 4,
    },
    identifier: {
      accepted: 480,
      accepted_seconds: 960,
      duty_std: 0.19,
      temp_span: 23.5,
      transition_seen: true,
      duty_segments: 7,
      raw_best_residual: 0.72,
      raw_runner_up_residual: 1.08,
      raw_candidates_passing: 2,
      trusted,
      distrust_count: 1,
      distrust_ratio: 0.02,
    },
    predictor: {
      active: true,
      disabled: false,
      x0: 249.8,
      xd: 252.1,
      residual_streak: 0,
      z0: 250.2,
      zd: 251.4,
      truncated: 1,
      model: predictorModel,
    },
    checkpoint: {
      schema_version: 2,
      revision: 12,
      provenance: "confirmed-online-fit",
      selected: {
        schema_version: "pid-sp-model-selection/v1",
        form: "ipdt",
        parameters: {
          K_i: 0.043,
          c0: -0.006,
          theta: 18,
        },
        delay_basin: {
          lower_s: 16,
          upper_s: 20,
          representative_s: 18,
          confidence_lower_s: 16,
          confidence_upper_s: 20,
          confidence_method: "moving-block-refit",
          confidence_resamples: 128,
          episode_count: 4,
          interior: true,
          blockers: [],
        },
        one_step_loss: 0.72,
        horizon_losses: [
          [3, 0.73],
          [15, 0.8],
        ],
        fold_losses: [0.72, 0.74],
        standard_error: 0.02,
        episode_ids: ["episode-a", "episode-b", "episode-c", "episode-d"],
        common_row_digest: "c".repeat(64),
        fit_corpus_digest: "d".repeat(64),
        configuration_digest: "e".repeat(64),
        comparison_threshold: 0.5,
        selection_margin: 0.1,
        confirmation_observed: 20,
        confirmation_required: 20,
        authorized: true,
        model_digest: "f".repeat(64),
      },
    },
    comparison: {
      forms: [
        {
          form: "ipdt",
          eligible: true,
          blockers: [],
          one_step_loss: 0.72,
          horizon_losses: [{ horizon_s: 3, loss: 0.73 }],
          fold_losses: [0.72, 0.74],
          standard_error: 0.02,
          basin_lower_s: 16,
          basin_upper_s: 20,
          confidence_lower_s: 16,
          confidence_upper_s: 20,
          confidence_method: "moving-block-refit",
        },
      ],
      best_form: "ipdt",
      comparison_threshold: 0.5,
      selection_margin: 0.1,
      selected_form: "ipdt",
      confirmation: {
        observed: 3,
        required: 4,
      },
      primary_blocker: null,
    },
    active_model: {
      form: "ipdt",
      model_digest: "f".repeat(64),
    },
    delay_evidence: {
      status: "delay-basin-stable",
      completed_episode_count: 4,
      evaluated_bound_s: 120,
      profile_form: "ipdt",
      raw_basin_lower_s: 16,
      raw_basin_upper_s: 20,
      raw_basin_representative_s: 18,
      confidence_lower_s: 16,
      confidence_upper_s: 20,
      confidence_method: "moving-block-refit",
      confidence_resamples: 128,
      blockers: [],
      authorized: true,
    },
    failure: null,
  };
}

test.beforeEach(async ({ page }) => {
  await freezeDate(page);
  await page.goto("/");
  await expect(page.locator('[data-pf="stage"]')).toBeVisible();
});

test("the board is laid out at the panel's width, not scaled into it", async ({ page }) => {
  const viewport = page.viewportSize();
  const width = viewport?.width ?? 0;
  expect(width).toBe(800);

  const board = await page.evaluate(() => {
    const el = document.querySelector<HTMLElement>('[data-pf="stage"]');
    if (el === null) throw new Error("no [data-pf=stage] on the page");
    const layoutWidth = el.offsetWidth;
    return {
      layoutWidth,
      scale: layoutWidth === 0 ? 1 : el.getBoundingClientRect().width / layoutWidth,
    };
  });

  // offsetWidth, not the client rect: a transform leaves the layout box alone,
  // so the rect cannot tell "800px board" from "1280px board shrunk to 0.62".
  expect(Math.abs(board.layoutWidth - width)).toBeLessThanOrEqual(1);
  expect(board.scale).toBeGreaterThan(0.99);
  expect(board.scale).toBeLessThan(1.01);

  mkdirSync(ARTIFACTS, { recursive: true });
  // Taller viewport for the capture only -- the panel scrolls, and an artifact
  // cut off at the fold is no use to a human. Both breakpoints are width-based,
  // so this is the same layout.
  await page.setViewportSize({ width, height: 1400 });
  await page
    .locator('[data-pf="stage"]')
    .screenshot({ path: `${ARTIFACTS}/dashboard-800x480.png`, animations: "disabled" });
});

test("the gauge is rendered at the size the breakpoint declares", async ({ page }) => {
  // The token and the box it lands in, compared against each other. A
  // breakpoint that sets --pf-gauge-size: 320px into a column 71px wide has
  // declared something no element can consume -- which is exactly the state
  // this file was written against, and which every "does the stylesheet
  // contain a @media block" assertion in the suite reports as fine.
  const gauge = await page.evaluate(() => {
    const dash = document.querySelector<HTMLElement>(".pf-dash");
    const svg = document.querySelector<SVGElement>('[data-pf="gauge"] svg');
    if (dash === null || svg === null) throw new Error("no gauge on the page");
    return {
      declared: Number.parseFloat(getComputedStyle(dash).getPropertyValue("--pf-gauge-size")),
      rendered: svg.getBoundingClientRect().width,
    };
  });
  expect(gauge.declared).toBeGreaterThan(0);
  expect(Math.abs(gauge.rendered - gauge.declared)).toBeLessThanOrEqual(1);
});

test("no control button is wider than the column that holds it", async ({ page }) => {
  // The squeeze this catches is silent: the buttons keep their own width and
  // simply overhang, so nothing overflows the PAGE and no landmark moves.
  const escapes = await page.evaluate(() => {
    const controls = document.querySelector<HTMLElement>('[data-pf="controls"]');
    if (controls === null) throw new Error("no control row on the page");
    const box = controls.getBoundingClientRect();
    return [...document.querySelectorAll<HTMLElement>(".pf-btn")]
      .map((el) => el.getBoundingClientRect())
      .filter((r) => r.left < box.left - 1 || r.right > box.right + 1)
      .map(
        (r) =>
          `button [${Math.round(r.left)}..${Math.round(r.right)}] escapes controls [${Math.round(box.left)}..${Math.round(box.right)}]`,
      );
  });
  expect(escapes, escapes.join("\n")).toEqual([]);
});

test("nothing is laid out wider than the panel", async ({ page }) => {
  const tooWide = await page.evaluate(() =>
    [...document.querySelectorAll<HTMLElement>("[data-pf]")]
      .filter((el) => el.offsetWidth > window.innerWidth + 1)
      .map(
        (el) =>
          `${el.dataset.pf}: laid out at ${el.offsetWidth}px in a ${window.innerWidth}px viewport`,
      ),
  );
  expect(tooWide, tooWide.join("\n")).toEqual([]);

  const overflow = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  }));
  expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.innerWidth);
});

test("the whole board is reachable by scrolling", async ({ page }) => {
  // 480px is shorter than this dashboard can be, so the panel is expected to
  // scroll. What is not acceptable is content the user cannot get to: .pf-dash
  // carries `overflow: hidden` for the decorative glow, so if the board is ever
  // pinned to a height its content exceeds, the excess is deleted with no
  // scrollbar anywhere to recover it.
  const reach = await page.evaluate(() => {
    const dash = document.querySelector<HTMLElement>(".pf-dash");
    const scroller = document.querySelector<HTMLElement>(".pf-shell-main");
    const controls = document.querySelector<HTMLElement>('[data-pf="controls"]');
    if (dash === null || scroller === null || controls === null) {
      throw new Error("no board, scroller or control row");
    }
    return {
      // The glow sits at bottom: -160px and is clipped on purpose, so the board
      // legitimately overhangs itself by that much -- and by no more.
      overhang: dash.scrollHeight - dash.clientHeight,
      scrollerReachesBoard:
        scroller.scrollHeight >= Math.round(dash.getBoundingClientRect().height) - 1,
      controlsWithinBoard: controls.offsetTop + controls.offsetHeight <= dash.clientHeight + 1,
    };
  });
  expect(reach.overhang).toBeLessThanOrEqual(160);
  expect(reach.scrollerReachesBoard).toBe(true);
  expect(reach.controlsWithinBoard).toBe(true);
});

test("no button label spills out of its button", async ({ page }) => {
  // scrollWidth against clientWidth: a label wider than its box does not
  // stretch the box and does not overflow the page, it just paints over the
  // button's own rounded border. Measured at 800x480 with the desktop 25px
  // still in force: "Shutdown" wanted 104px inside a 96px button.
  const spills = await page.evaluate(() =>
    [...document.querySelectorAll<HTMLElement>(".pf-btn")]
      .filter((el) => el.scrollWidth > el.clientWidth)
      .map(
        (el) =>
          `"${(el.textContent ?? "").trim()}" needs ${el.scrollWidth}px inside a ${el.clientWidth}px button`,
      ),
  );
  expect(spills, spills.join("\n")).toEqual([]);
});

test("the hopper still has a level bar to read", async ({ page }) => {
  // `.pf-dash-hopper-track` is flex: 1 inside its card, so it renders at
  // whatever height the card was handed. When the right column wraps onto its
  // own line the card is sized by its content, and the bar collapsed to zero --
  // the card kept its "HOPPER", its "70%" and its "LEVEL OK" and simply lost
  // the one element that shows a level. Nothing else in this suite can see
  // that: no landmark moves, no box overflows, and the numbers are still right.
  const track = await page.evaluate(() => {
    const el = document.querySelector<HTMLElement>(".pf-dash-hopper-track");
    if (el === null) throw new Error("no hopper track on the page");
    return el.getBoundingClientRect().height;
  });
  expect(track).toBeGreaterThanOrEqual(80);
});

test("every control button is a usable touch target", async ({ page }) => {
  const heights = await page.evaluate(() =>
    [...document.querySelectorAll<HTMLElement>(".pf-btn")].map(
      (el) => el.getBoundingClientRect().height,
    ),
  );
  expect(heights.length).toBeGreaterThan(0);
  for (const h of heights) expect(h).toBeGreaterThanOrEqual(44);
});

test("an over-tall dialog stays on screen and every item stays reachable", async ({ page }) => {
  // Nothing bounded a dialog's height. The real Prime menu fits everywhere
  // (measured: 457px against this 480px panel), but only by 12px -- one more
  // item, a wrapped label or a larger font and it would have gone over, and
  // .pf-dash is overflow:hidden so the excess is clipped rather than scrolled.
  // Verified in a real browser at 30 items: without the cap the dialog
  // measured 0..487 in a 480px viewport; with it, 0..480.
  //
  // The markup is what ActionMenu renders -- the component cannot be driven
  // here because demoData pins the grill to Hold, and Prime only appears when
  // it is stopped.
  const scrimSel = ".pf-modal-scrim";
  await page.evaluate(() => {
    const host = document.querySelector(".pf-dash-controls");
    if (host === null) throw new Error("no control row to host the scrim");
    const scrim = document.createElement("div");
    scrim.className = "pf-modal-scrim";
    const items = Array.from({ length: 30 }, (_, i) => `Prime ${i}g & Startup`);
    scrim.innerHTML =
      '<div class="pf-modal"><div class="pf-modal-title">Prime</div>' +
      '<div class="pf-menu-list">' +
      items.map((l) => `<button class="pf-modal-btn pf-menu-item">${l}</button>`).join("") +
      '</div><div class="pf-modal-actions">' +
      '<button class="pf-modal-btn">Cancel</button></div></div>';
    host.appendChild(scrim);
  });

  const read = () =>
    page.evaluate(() => {
      const modal = document.querySelector<HTMLElement>(".pf-modal");
      const list = document.querySelector<HTMLElement>(".pf-menu-list");
      const items = [...document.querySelectorAll<HTMLElement>(".pf-menu-item")];
      const cancel = [...document.querySelectorAll<HTMLElement>(".pf-modal-actions .pf-modal-btn")];
      if (modal === null || list === null) throw new Error("no dialog");
      const onScreen = (el: HTMLElement) => {
        const r = el.getBoundingClientRect();
        return r.top >= 0 && r.bottom <= window.innerHeight;
      };
      const r = modal.getBoundingClientRect();
      return {
        modalTop: r.top,
        modalBottom: r.bottom,
        viewportH: window.innerHeight,
        lastItemOnScreen: onScreen(items[items.length - 1]),
        cancelOnScreen: onScreen(cancel[cancel.length - 1]),
        listScrolls: list.scrollHeight > list.clientHeight,
      };
    });

  const before = await read();
  // The dialog itself never leaves the screen, however tall its content.
  expect(before.modalTop).toBeGreaterThanOrEqual(0);
  expect(before.modalBottom).toBeLessThanOrEqual(before.viewportH);
  // Too tall to show at once, so the list -- not the whole dialog -- scrolls.
  expect(before.listScrolls).toBe(true);
  expect(before.lastItemOnScreen).toBe(false);

  await page.locator(".pf-menu-list").hover();
  await page.mouse.wheel(0, 2000);
  await page.waitForTimeout(200);

  const after = await read();
  // The far end is reachable, and Cancel never went with it: the list scrolls
  // inside a fixed frame, so the title and the buttons stay put.
  expect(after.lastItemOnScreen).toBe(true);
  expect(after.cancelOnScreen).toBe(true);
  expect(before.cancelOnScreen).toBe(true);

  await page.evaluate((sel) => document.querySelector(sel)?.remove(), scrimSel);
});

test("one report trigger stays after Hopper and the full panel is reachable at both target sizes", async ({
  page,
}) => {
  const report = evidenceReport("evaluating", EXACT_DIGEST, EXACT_DECISION, {
    roleGeneration: 44,
  });
  await page.route("**/api/settings", (route) =>
    route.fulfill({
      json: {
        settings: {
          controller: { selected: "mpc", config: { mpc: { T_amb: 20 } } },
          safety: { maxtemp: 600 },
        },
      },
    }),
  );
  await page.route("**/api/model-evidence/report", (route) => route.fulfill({ json: report }));
  await page.reload();

  for (const viewport of [
    { width: 800, height: 480 },
    { width: 1280, height: 720 },
  ]) {
    await page.setViewportSize(viewport);
    const trigger = page.getByRole("button", { name: "MPC learning: evaluating" });
    await trigger.scrollIntoViewIfNeeded();
    await expect(trigger).toBeVisible();
    await expect(page.locator('[data-pf="rightCol"]')).toContainText("MPC learning: evaluating");
    await expect(page.locator('[data-pf="controls"]')).not.toContainText("MPC learning:");
    const hopper = page.locator(".pf-dash-hopper");
    await expect(hopper).toBeVisible();
    expect(await hopper.evaluate((node) => node.nextElementSibling?.textContent)).toContain(
      "MPC learning: evaluating",
    );

    const triggerBox = await trigger.boundingBox();
    expect(triggerBox).not.toBeNull();
    expect(triggerBox!.x).toBeGreaterThanOrEqual(0);
    expect(triggerBox!.x + triggerBox!.width).toBeLessThanOrEqual(viewport.width);
    expect(triggerBox!.y).toBeGreaterThanOrEqual(0);
    expect(triggerBox!.y + triggerBox!.height).toBeLessThanOrEqual(viewport.height);

    await trigger.click();
    const dialog = page.getByRole("dialog", { name: "MPC model learning" });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText("Role generation: 44", { exact: true })).toBeVisible();
    await expect(dialog.getByText("Candidate generation: 5", { exact: true })).toBeVisible();
    const dialogBox = await dialog.boundingBox();
    expect(dialogBox).not.toBeNull();
    expect(dialogBox!.x).toBeGreaterThanOrEqual(0);
    expect(dialogBox!.x + dialogBox!.width).toBeLessThanOrEqual(viewport.width);
    expect(dialogBox!.y).toBeGreaterThanOrEqual(0);
    expect(dialogBox!.y + dialogBox!.height).toBeLessThanOrEqual(viewport.height);

    const finalSection = dialog.getByRole("heading", { name: "Model ownership" });
    await finalSection.scrollIntoViewIfNeeded();
    await expect(finalSection).toBeVisible();
    await dialog.getByRole("button", { name: "Close MPC model learning" }).click();
  }
});

test("PID-SP learning stays reachable and controller-authored at both target sizes", async ({
  page,
}) => {
  await page.route("**/api/settings", (route) =>
    route.fulfill({
      json: {
        settings: {
          controller: { selected: "pid_sp", config: { pid_sp: {} } },
          safety: { maxtemp: 600 },
        },
      },
    }),
  );
  await page.route("**/api/pid-sp-learning/report", (route) =>
    route.fulfill({ json: pidSpReport() }),
  );

  for (const viewport of [
    { width: 800, height: 480 },
    { width: 1280, height: 720 },
  ]) {
    await page.setViewportSize(viewport);
    await page.reload();

    const rightColumn = page.locator('[data-pf="rightCol"]');
    const hopper = page.locator(".pf-dash-hopper");
    const trigger = page.getByRole("button", {
      name: "PID-SP learning: active",
    });
    await trigger.scrollIntoViewIfNeeded();
    await expect(trigger).toBeVisible();
    await expect(rightColumn).toContainText("PID-SP learning: active");
    expect(await hopper.evaluate((node) => node.nextElementSibling?.textContent)).toContain(
      "PID-SP learning: active",
    );

    const [rightColumnBox, triggerBox] = await Promise.all([
      rightColumn.boundingBox(),
      trigger.boundingBox(),
    ]);
    expect(rightColumnBox).not.toBeNull();
    expect(triggerBox).not.toBeNull();
    expect(triggerBox!.x).toBeGreaterThanOrEqual(rightColumnBox!.x);
    expect(triggerBox!.x + triggerBox!.width).toBeLessThanOrEqual(
      rightColumnBox!.x + rightColumnBox!.width,
    );

    const dashboardBefore = await page.locator('[data-pf="stage"]').innerText();
    await trigger.click();

    const dialog = page.getByRole("dialog", { name: "PID-SP model learning" });
    const title = dialog.getByRole("heading", {
      name: "PID-SP model learning",
    });
    const close = dialog.getByRole("button", {
      name: "Close PID-SP model learning",
    });
    await expect(dialog).toBeVisible();
    await expect(title).toBeVisible();
    await expect(close).toBeVisible();
    await expect(dialog.getByText("Trusted model: ipdt", { exact: true })).toBeVisible();
    await expect(dialog.getByText("Confirmation progress: 3 of 4")).toBeVisible();

    const dialogGeometry = await dialog.evaluate((node) => {
      const rect = node.getBoundingClientRect();
      return {
        left: rect.left,
        right: rect.right,
        top: rect.top,
        bottom: rect.bottom,
        clientWidth: node.clientWidth,
        clientHeight: node.clientHeight,
        scrollHeight: node.scrollHeight,
        scrimPosition: getComputedStyle(node.parentElement!).position,
        documentWidth: document.documentElement.scrollWidth,
        viewportWidth: window.innerWidth,
        viewportHeight: window.innerHeight,
      };
    });
    expect(dialogGeometry.scrimPosition).toBe("fixed");
    expect(dialogGeometry.left).toBeGreaterThanOrEqual(0);
    expect(dialogGeometry.right).toBeLessThanOrEqual(dialogGeometry.viewportWidth);
    expect(dialogGeometry.top).toBeGreaterThanOrEqual(0);
    expect(dialogGeometry.bottom).toBeLessThanOrEqual(dialogGeometry.viewportHeight);
    expect(dialogGeometry.documentWidth).toBeLessThanOrEqual(dialogGeometry.viewportWidth);

    const gates = dialog.getByRole("heading", { name: "Excitation gates" }).locator("..");
    await expect(gates.getByRole("row", { name: /Accepted samples Met 480 360/ })).toBeVisible();
    const checkpoint = dialog
      .getByRole("heading", { name: "Durable checkpoint" })
      .locator("xpath=ancestor::section[1]");
    await expect(checkpoint.getByRole("row", { name: /K_i 0.043/ })).toBeVisible();

    if (viewport.width === 800) {
      expect(dialogGeometry.scrollHeight).toBeGreaterThan(dialogGeometry.clientHeight);
      const pageScroller = page.locator(".pf-shell-main");
      const pageScrollBefore = await pageScroller.evaluate((node) => node.scrollTop);
      const dialogScrollBefore = await dialog.evaluate((node) => node.scrollTop);
      await dialog.hover();
      await page.mouse.wheel(0, 2_000);
      await expect
        .poll(() => dialog.evaluate((node) => node.scrollTop))
        .toBeGreaterThan(dialogScrollBefore);
      const dialogScrollAfter = await dialog.evaluate((node) => node.scrollTop);
      expect(await pageScroller.evaluate((node) => node.scrollTop)).toBe(pageScrollBefore);

      const finalSection = dialog.getByRole("heading", {
        name: "Predictor diagnostics",
      });
      const [scrolledDialogBox, finalSectionBox] = await Promise.all([
        dialog.boundingBox(),
        finalSection.boundingBox(),
      ]);
      expect(scrolledDialogBox).not.toBeNull();
      expect(finalSectionBox).not.toBeNull();
      expect(finalSectionBox!.y).toBeGreaterThanOrEqual(scrolledDialogBox!.y);
      expect(finalSectionBox!.y + finalSectionBox!.height).toBeLessThanOrEqual(
        scrolledDialogBox!.y + scrolledDialogBox!.height,
      );
      await expect(dialog.getByText("Predictor model: ipdt", { exact: true })).toBeVisible();

      await page.mouse.wheel(0, -2_000);
      await expect
        .poll(() => dialog.evaluate((node) => node.scrollTop))
        .toBeLessThan(dialogScrollAfter);
      const [returnedDialogBox, titleBox, closeBox] = await Promise.all([
        dialog.boundingBox(),
        title.boundingBox(),
        close.boundingBox(),
      ]);
      expect(returnedDialogBox).not.toBeNull();
      expect(titleBox).not.toBeNull();
      expect(closeBox).not.toBeNull();
      for (const box of [titleBox!, closeBox!]) {
        expect(box.y).toBeGreaterThanOrEqual(returnedDialogBox!.y);
        expect(box.y + box.height).toBeLessThanOrEqual(
          returnedDialogBox!.y + returnedDialogBox!.height,
        );
      }
    } else {
      expect(dialogGeometry.clientWidth).toBeGreaterThan(0);
      expect(await dialog.evaluate((node) => node.scrollWidth)).toBeLessThanOrEqual(
        dialogGeometry.clientWidth,
      );
      for (const section of [gates, checkpoint]) {
        const columnHeaders = section.getByRole("columnheader");
        expect(await columnHeaders.count()).toBe(4);
        const tableWrapper = section.getByRole("table").locator("..");
        await expect(tableWrapper).toBeVisible();
        const wrapperGeometry = await tableWrapper.evaluate((node) => ({
          clientWidth: node.clientWidth,
          scrollWidth: node.scrollWidth,
        }));
        expect(wrapperGeometry.clientWidth).toBeGreaterThan(0);
        expect(wrapperGeometry.scrollWidth).toBeLessThanOrEqual(wrapperGeometry.clientWidth);
        const boxes = await columnHeaders.evaluateAll((nodes) =>
          nodes.map((node) => {
            const rect = node.getBoundingClientRect();
            return { x: rect.x, y: rect.y };
          }),
        );
        expect(new Set(boxes.map(({ x }) => Math.round(x))).size).toBe(4);
        expect(
          Math.max(...boxes.map(({ y }) => y)) - Math.min(...boxes.map(({ y }) => y)),
        ).toBeLessThan(2);
      }
    }

    await expect(dialog.getByRole("button", { name: "Activate exact model" })).toHaveCount(0);
    await expect(dialog.getByRole("button", { name: "Roll back to explicit owner" })).toHaveCount(
      0,
    );
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(trigger).toBeFocused();
    expect(await page.locator('[data-pf="stage"]').innerText()).toBe(dashboardBefore);
  }
});

test("passive automatic transitions never expose reviewed activation controls", async ({
  page,
}) => {
  let report = evidenceReport("collecting", EXACT_DIGEST, EXACT_DECISION);
  await page.route("**/api/settings", (route) =>
    route.fulfill({
      json: {
        settings: {
          controller: { selected: "mpc", config: { mpc: { T_amb: 20 } } },
          safety: { maxtemp: 600 },
        },
      },
    }),
  );
  await page.route("**/api/model-evidence/report", (route) => route.fulfill({ json: report }));

  for (const [status, label] of [
    ["warming", "Warming"],
    ["collecting", "Collecting"],
    ["fitting", "Fitting"],
    ["evaluating", "Evaluating"],
    ["interrupted", "Interrupted"],
    ["qualified", "Qualified"],
    ["activating", "Activating"],
    ["active", "Active"],
  ] as const) {
    report = evidenceReport(status, EXACT_DIGEST, EXACT_DECISION, {
      origin: "passive-online",
      roleGeneration: 20,
    });
    await page.reload();
    const trigger = page.getByRole("button", {
      name: `MPC learning: ${label.toLowerCase()}`,
    });
    await trigger.scrollIntoViewIfNeeded();
    await trigger.click();
    const dialog = page.getByRole("dialog", { name: "MPC model learning" });
    await expect(dialog.getByText(label, { exact: true })).toBeVisible();
    await expect(dialog.getByText("Mode: passive-online")).toBeVisible();
    await expect(dialog.getByLabel("Type the exact candidate digest")).toHaveCount(0);
    await expect(dialog.getByLabel("Type the exact confidence decision ID")).toHaveCount(0);
    await expect(dialog.getByRole("button", { name: "Activate exact model" })).toHaveCount(0);
    await dialog.getByRole("button", { name: "Close MPC model learning" }).click();
  }
});

test("automatic calibration exposes causal progress and rolls back only to explicit owner", async ({
  page,
}) => {
  let report = evidenceReport("interrupted", EXACT_DIGEST, EXACT_DECISION, {
    origin: "operator-calibration",
    roleGeneration: 4,
  });
  let activationRequests = 0;
  await page.route("**/api/settings", (route) =>
    route.fulfill({
      json: {
        settings: {
          controller: { selected: "mpc", config: { mpc: { T_amb: 20 } } },
          safety: { maxtemp: 600 },
        },
      },
    }),
  );
  await page.route("**/api/model-evidence/report", (route) => route.fulfill({ json: report }));
  await page.route("**/api/model-evidence/activate", async (route) => {
    activationRequests += 1;
    await route.fulfill({ status: 404 });
  });
  await page.route("**/api/model-evidence/rollback", async (route) => {
    expect(route.request().postDataJSON()).toEqual({ reason: "active-solve-failed" });
    const fallback = evidenceReport("fallback", EXACT_DIGEST, EXACT_DECISION, {
      origin: "operator-calibration",
      roleGeneration: 6,
    });
    report = {
      ...fallback,
      activation: {
        ...fallback.activation,
        phase: "aborted",
        reason: "active-solve-failed",
      },
    };
    await route.fulfill({
      json: {
        accepted: true,
        active_kind: "grey-box",
        decision_id: EXACT_DECISION,
        reason: "active-solve-failed",
        role_generation: 6,
        rollback_digest: "c".repeat(64),
      },
    });
  });

  await page.reload();
  let trigger = page.getByRole("button", { name: "MPC learning: interrupted" });
  await trigger.scrollIntoViewIfNeeded();
  await trigger.click();
  let dialog = page.getByRole("dialog", { name: "MPC model learning" });
  await expect(dialog).toContainText("Evaluation epoch: 2");
  await expect(dialog).toContainText("Evaluation round: 1");
  await expect(dialog).toContainText("Completed horizons: 3, 15");
  await expect(dialog).toContainText("Wins: 1 / 2");
  await expect(dialog).toContainText("Resumed from previous cook: yes");
  await expect(dialog).toContainText("Pending origins: none");
  await dialog.getByRole("button", { name: "Close MPC model learning" }).click();

  report = evidenceReport("qualified", EXACT_DIGEST, EXACT_DECISION, {
    origin: "operator-calibration",
    roleGeneration: 4,
  });
  await page.reload();
  trigger = page.getByRole("button", { name: "MPC learning: qualified" });
  await trigger.scrollIntoViewIfNeeded();
  await trigger.click();
  dialog = page.getByRole("dialog", { name: "MPC model learning" });
  await expect(dialog).toContainText("Wins: 2 / 2");
  await expect(dialog).toContainText("challenger-e2e-5");
  await expect(dialog).toContainText("segment-e2e-5");
  await expect(dialog.getByLabel("Type the exact candidate digest")).toHaveCount(0);
  await expect(dialog.getByLabel("Type the exact confidence decision ID")).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: "Activate exact model" })).toHaveCount(0);
  await dialog.getByRole("button", { name: "Close MPC model learning" }).click();
  expect(activationRequests).toBe(0);

  report = evidenceReport("active", EXACT_DIGEST, EXACT_DECISION, {
    origin: "operator-calibration",
    roleGeneration: 5,
  });
  await page.reload();
  await page.getByRole("button", { name: "MPC learning: active" }).click();
  const ownership = page.getByRole("heading", { name: "Model ownership" }).locator("..");
  await expect(ownership).toContainText("Rollback owner");
  await expect(ownership).toContainText("c".repeat(64));
  await page.getByLabel("Required rollback reason").fill("active-solve-failed");
  await page.getByRole("button", { name: "Roll back to explicit owner" }).click();
  await expect(page.getByText("Fallback", { exact: true })).toBeVisible();
  await expect(page.getByText("Reason: active-solve-failed")).toBeVisible();
  expect(activationRequests).toBe(0);
});

test("native rejection and structured failure remain backend-authored", async ({ page }) => {
  let report = evidenceReport("evaluating", EXACT_DIGEST, EXACT_DECISION, {
    origin: "operator-calibration",
    roleGeneration: 30,
  });
  await page.route("**/api/settings", (route) =>
    route.fulfill({
      json: {
        settings: {
          controller: { selected: "mpc", config: { mpc: { T_amb: 20 } } },
          safety: { maxtemp: 600 },
        },
      },
    }),
  );
  await page.route("**/api/model-evidence/report", (route) => route.fulfill({ json: report }));

  await page.reload();
  await page.getByRole("button", { name: "MPC learning: evaluating" }).click();
  await expect(page.getByRole("button", { name: "Activate exact model" })).toHaveCount(0);

  report = {
    ...report,
    status: "error",
    errors: ["native-build-failed"],
    failure: {
      code: "activation-terminal",
      detail: "candidate handle could not load ABI v2",
      terminal: true,
    },
    candidate: {
      ...report.candidate!,
      assessment: {
        ...report.candidate!.assessment!,
        native_build: "failed",
        confidence_accepted: false,
        rejection_reasons: ["native-build"],
      },
    },
  };
  await page.reload();
  await page.getByRole("button", { name: "MPC learning: error" }).click();
  await expect(page.getByRole("alert")).toContainText("native-build-failed");
  await expect(page.getByRole("alert")).toContainText("candidate handle could not load ABI v2");
  await expect(page.getByText("Native build: failed")).toBeVisible();
});
