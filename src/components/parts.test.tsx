import { afterEach, describe, expect, mock, test } from "bun:test";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { ApprovalCard } from "./ApprovalCard";
import { AppFrame } from "./AppFrame";
import { Composer } from "./Composer";
import { ProgressCard } from "./ProgressCard";
import { ResultLine } from "./ResultLine";
import { Steps } from "./Steps";
import { UserMessage } from "./UserMessage";
import { WorkerMark } from "./WorkerMark";
import { WorkerMessage } from "./WorkerMessage";

afterEach(cleanup);

describe("WorkerMark", () => {
  test("is an image named Worker in three sizes", () => {
    render(
      <>
        <WorkerMark size="sm" />
        <WorkerMark size="md" />
        <WorkerMark size="lg" />
      </>,
    );
    const marks = screen.getAllByRole("img", { name: "Worker" });
    expect(marks.map((mark) => mark.getAttribute("width"))).toEqual(["24", "32", "64"]);
  });
});

describe("WorkerMark dark tone", () => {
  test("fills the square dark and draws the screen light, with no second frame", () => {
    render(<WorkerMark size="sm" tone="dark" label="ClientOps" />);
    const mark = screen.getByRole("img", { name: "ClientOps" });
    const [square, display] = Array.from(mark.querySelectorAll("rect"));
    expect(square.getAttribute("fill")).toBe("var(--color-text-primary)");
    expect(square.getAttribute("stroke")).toBeNull();
    expect(display.getAttribute("stroke")).toBe("var(--color-background-surface)");
  });
});

describe("AppFrame logo", () => {
  test("shows only the dark product mark beside the name", () => {
    render(<AppFrame title="t" userName="u" clients={[]} recent={[]} composer={null} />);
    const marks = screen.getAllByRole("img", { name: "ClientOps" });
    expect(marks).toHaveLength(1);
    expect(marks[0].querySelector("rect")?.getAttribute("fill")).toBe("var(--color-text-primary)");
  });
});

describe("Composer", () => {
  test("labels the input, keeps the placeholder, shows the approval promise and the client", () => {
    render(<Composer clientName="Kumo Coffee" onSubmit={() => {}} />);
    expect(screen.getByRole("textbox", { name: "Describe a job" })).toBeTruthy();
    expect(screen.getByText("Asks before saving")).toBeTruthy();
    expect(screen.getByText("Works on Kumo Coffee's own computer with its logins only.")).toBeTruthy();
  });

  test("uses the follow-up placeholder while a job is running", () => {
    render(<Composer clientName="Kumo Coffee" isWorking onSubmit={() => {}} />);
    expect(screen.getByRole("textbox", { name: "Message the worker" })).toBeTruthy();
  });
});

describe("UserMessage and WorkerMessage", () => {
  test("show plain words, the worker with its mark", () => {
    render(
      <>
        <UserMessage>Sync today&apos;s supplier stock. Ask me before saving.</UserMessage>
        <WorkerMessage>12 products are different. Nothing is saved yet.</WorkerMessage>
      </>,
    );
    expect(screen.getByText("Sync today's supplier stock. Ask me before saving.")).toBeTruthy();
    expect(screen.getByText("12 products are different. Nothing is saved yet.")).toBeTruthy();
    expect(screen.getByRole("img", { name: "Worker" })).toBeTruthy();
  });
});

describe("ProgressCard", () => {
  test("says what is happening, fills by finished steps, and stops", () => {
    const onStop = mock(() => {});
    render(<ProgressCard title="Comparing with the store admin" done={2} total={5} onStop={onStop} />);
    expect(screen.getByText("Comparing with the store admin")).toBeTruthy();
    const bar = screen.getByRole("progressbar", { name: "Job progress" });
    expect(bar.getAttribute("aria-valuenow")).toBe("2");
    expect(bar.getAttribute("aria-valuemax")).toBe("5");
    expect(screen.getByText("Step 3 of 5")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Stop the job" }));
    expect(onStop).toHaveBeenCalledTimes(1);
  });

  test("offers to open Activity only while it is closed", () => {
    const onOpenActivity = mock(() => {});
    const { rerender } = render(
      <ProgressCard title="Reading stock" done={0} total={5} onStop={() => {}} onOpenActivity={onOpenActivity} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Show activity" }));
    expect(onOpenActivity).toHaveBeenCalledTimes(1);
    rerender(<ProgressCard title="Reading stock" done={0} total={5} onStop={() => {}} onOpenActivity={onOpenActivity} isActivityOpen />);
    expect(screen.queryByRole("button", { name: "Show activity" })).toBeNull();
  });
});

describe("Steps", () => {
  test("lists done, now and next steps with who decided", () => {
    render(
      <Steps
        steps={[
          { label: "Read today's stock", detail: "Decided by Claude", state: "done" },
          { label: "Comparing with the store admin", detail: "Claude is deciding", state: "now" },
          { label: "Ask you before saving", detail: "You decide", state: "next" },
        ]}
      />,
    );
    const list = screen.getByRole("list", { name: "Steps" });
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(items[0].textContent).toContain("Read today's stock");
    expect(items[0].textContent).toContain("Decided by Claude");
    expect(items[1].textContent).toContain("Claude is deciding");
    expect(items[2].textContent).toContain("You decide");
  });
});

describe("ApprovalCard", () => {
  test("asks a question with one main button and a quiet way out", () => {
    const onConfirm = mock(() => {});
    const onDecline = mock(() => {});
    render(
      <ApprovalCard
        question="Save 12 stock changes to the store admin?"
        confirmLabel="Save changes"
        onConfirm={onConfirm}
        onDecline={onDecline}
      />,
    );
    const card = screen.getByRole("group", { name: "Save 12 stock changes to the store admin?" });
    fireEvent.click(within(card).getByRole("button", { name: "Save changes" }));
    fireEvent.click(within(card).getByRole("button", { name: "Not now" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onDecline).toHaveBeenCalledTimes(1);
  });
});

describe("ResultLine", () => {
  test("marks done, stopped and blocked with different named shapes", () => {
    render(
      <>
        <ResultLine kind="done">Done. 12 products updated.</ResultLine>
        <ResultLine kind="stopped">Stopped before saving.</ResultLine>
        <ResultLine kind="blocked">Not allowed for this client.</ResultLine>
      </>,
    );
    expect(screen.getByRole("img", { name: "Done" })).toBeTruthy();
    expect(screen.getByRole("img", { name: "Stopped" })).toBeTruthy();
    expect(screen.getByRole("img", { name: "Not allowed" })).toBeTruthy();
    expect(screen.getByText("Not allowed for this client.")).toBeTruthy();
  });
});

describe("AppFrame", () => {
  const frame = {
    title: "Supplier stock sync",
    subtitle: "Kumo Coffee Roasters",
    userName: "Aoi Tanaka",
    clients: [
      { name: "Kumo Coffee Roasters", href: "/c/kumo", status: "working" as const, isSelected: true },
      { name: "Hinata Skincare", href: "/c/hinata", status: "waiting" as const, approvals: 1 },
      { name: "Sora Outdoor", href: "/c/sora", status: "idle" as const },
    ],
    recent: [
      { label: "Supplier stock sync", href: "/j/1", isSelected: true },
      { label: "Weekly price check", href: "/j/2" },
    ],
    composer: <Composer clientName="Kumo Coffee" onSubmit={() => {}} />,
  };

  test("has the sidebar with jobs, clients with state and approvals, recent jobs, and the user", () => {
    render(<AppFrame {...frame}>conversation</AppFrame>);
    expect(screen.getByRole("link", { name: /New job/ })).toBeTruthy();
    expect(screen.getByRole("link", { name: /Search jobs/ })).toBeTruthy();
    expect(screen.getByRole("link", { name: /Kumo Coffee Roasters/ })).toBeTruthy();
    expect(screen.getByLabelText("1 waiting for your approval")).toBeTruthy();
    expect(screen.getByRole("link", { name: /Weekly price check/ })).toBeTruthy();
    expect(screen.getByText("Aoi Tanaka")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Supplier stock sync" })).toBeTruthy();
    expect(screen.getByText("conversation")).toBeTruthy();
    expect(screen.getByRole("textbox", { name: "Describe a job" })).toBeTruthy();
  });

  test("the sidebar closes from its own button and opens again from the header", () => {
    render(<AppFrame {...frame}>conversation</AppFrame>);
    const nav = screen.getByRole("navigation", { name: "Side navigation" });
    expect(screen.queryByRole("button", { name: "Open sidebar" })).toBeNull();

    fireEvent.click(within(nav).getByRole("button", { name: "Close sidebar" }));

    expect(nav.closest("[data-motion=sidebar]")?.hasAttribute("inert")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Open sidebar" }));
    expect(nav.closest("[data-motion=sidebar]")?.hasAttribute("inert")).toBe(false);
    expect(screen.queryByRole("button", { name: "Open sidebar" })).toBeNull();
  });

  test("setup that is not finished is one row in the sidebar that opens the setup", () => {
    const onNewJob = mock(() => {});
    render(
      <AppFrame {...frame} setup={{ done: 1, total: 3 }} onNewJob={onNewJob}>
        conversation
      </AppFrame>,
    );
    const nav = screen.getByRole("navigation", { name: "Side navigation" });
    const row = within(nav).getByRole("link", { name: /Finish setup/ });
    expect(row.textContent).toContain("1 of 3");
    expect(within(nav).queryByRole("progressbar")).toBeNull();
    fireEvent.click(row);
    expect(onNewJob).toHaveBeenCalledTimes(1);
  });

  test("shows the Activity panel only when there is activity, and closes it", () => {
    const onCloseActivity = mock(() => {});
    const { rerender } = render(<AppFrame {...frame}>conversation</AppFrame>);
    expect(screen.queryByRole("complementary", { name: "Activity" })).toBeNull();
    rerender(
      <AppFrame {...frame} activity={<span>steps here</span>} onCloseActivity={onCloseActivity}>
        conversation
      </AppFrame>,
    );
    const panel = screen.getByRole("complementary", { name: "Activity" });
    expect(within(panel).getByText("steps here")).toBeTruthy();
    fireEvent.click(within(panel).getByRole("button", { name: "Close activity" }));
    expect(onCloseActivity).toHaveBeenCalledTimes(1);
  });
});
