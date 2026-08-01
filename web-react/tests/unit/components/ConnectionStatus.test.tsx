import { describe, expect, it } from "@rstest/core";
import { render, screen } from "@testing-library/react";
import { ConnectionStatus } from "../../../src/components/ConnectionStatus";

describe("ConnectionStatus", () => {
  it("shows a connecting message with the target URL while phase is connecting", () => {
    render(<ConnectionStatus phase="connecting" targetUrl="http://pifire.local:5000" />);

    expect(screen.getByText("Connecting to PiFire…")).toBeInTheDocument();
    expect(screen.getByText("Contacting")).toBeInTheDocument();
    expect(screen.getByText("http://pifire.local:5000")).toBeInTheDocument();
    expect(screen.queryByText("PiFire not reachable")).not.toBeInTheDocument();
    expect(screen.queryByText(/Start PiFire/)).not.toBeInTheDocument();
  });

  it("shows an unreachable message with the target URL and retry hint", () => {
    render(<ConnectionStatus phase="unreachable" targetUrl="http://192.168.1.50:5000" />);

    expect(screen.getByText("PiFire not reachable")).toBeInTheDocument();
    expect(screen.getByText("Tried")).toBeInTheDocument();
    expect(screen.getByText("http://192.168.1.50:5000")).toBeInTheDocument();
    expect(screen.getByText(/Start PiFire/)).toBeInTheDocument();
    expect(screen.getByText(/Retrying/)).toBeInTheDocument();
  });
});
