import { fireEvent, render, screen } from "@testing-library/react";

import { ConfirmDialog } from "./ConfirmDialog";
import { Pnl } from "./ui";

describe("Pnl", () => {
  it("shows sign and an icon, not only color", () => {
    const { container, rerender } = render(<Pnl value={0.05} />);
    expect(screen.getByText("+%5,00")).toBeInTheDocument();
    expect(container.querySelector("svg")).not.toBeNull();
    expect(container.firstElementChild).toHaveClass("text-gain");

    rerender(<Pnl value={-12.5} kind="money" />);
    expect(screen.getByText("−12,50")).toBeInTheDocument();
    expect(container.firstElementChild).toHaveClass("text-loss");
  });

  it("renders a dash for missing values", () => {
    render(<Pnl value={null} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });
});

describe("ConfirmDialog", () => {
  it("enables the action only after the exact account name is typed", () => {
    const onConfirm = vi.fn();
    render(
      <ConfirmDialog
        open
        title="Alım iznini kapat"
        description="test"
        confirmText="paper:donchian"
        actionLabel="Alımı kapat"
        danger
        onConfirm={onConfirm}
        onClose={() => {}}
      />,
    );
    const button = screen.getByRole("button", { name: "Alımı kapat" });
    const input = screen.getByLabelText("Onay metni");
    expect(button).toBeDisabled();

    fireEvent.change(input, { target: { value: "paper" } });
    expect(button).toBeDisabled();

    fireEvent.change(input, { target: { value: "paper:donchian" } });
    expect(button).toBeEnabled();
    fireEvent.click(button);
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("renders nothing when closed", () => {
    const { container } = render(
      <ConfirmDialog open={false} title="x" description="x" confirmText="x" actionLabel="x" onConfirm={() => {}} onClose={() => {}} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
