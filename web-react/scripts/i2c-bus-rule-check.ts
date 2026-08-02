// Evaluates the real i2cBusError against bus configs read from stdin as a
// JSON array, printing one accept/reject verdict per case as a JSON array of
// booleans. Not part of the app build -- invoked only by
// tests/web/test_i2c_bus_rule_parity.py, which is the only thing that runs
// the wizard's TS validation rules for comparison against Python's rather
// than trusting a hand-copied expectation of what they do.
import { i2cBusError, type I2cBusValue } from "../src/helpers/wizard/i2cBusTypes";

async function main() {
  const cases = JSON.parse(await Bun.stdin.text()) as unknown[];
  const verdicts = cases.map((bus) => i2cBusError(bus as I2cBusValue) === null);
  console.log(JSON.stringify(verdicts));
}

main();
