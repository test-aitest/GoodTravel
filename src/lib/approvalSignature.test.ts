import { describe, expect, test } from "bun:test";
import vector from "../../agent/tests/contract/approval_signature.json";
import { approvalBody, approvalSignature } from "./approvalSignature";

describe("approvalSignature", () => {
  test("matches RFC 4231 test case 1 for HMAC-SHA256", () => {
    const key = new Uint8Array(20).fill(0x0b);
    expect(approvalSignature(key, "Hi There")).toBe("sha256=b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7");
  });

  test("matches RFC 4231 test case 2 for HMAC-SHA256", () => {
    expect(approvalSignature("Jefe", "what do ya want for nothing?")).toBe(
      "sha256=5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843",
    );
  });

  test("gives the same header as the agent's Python check for the same raw body", () => {
    expect(approvalSignature(vector.secret, vector.body)).toBe(vector.header);
  });

  test("changes when the body or the secret changes", () => {
    expect(approvalSignature(vector.secret, `${vector.body} `)).not.toBe(vector.header);
    expect(approvalSignature("another-secret", vector.body)).not.toBe(vector.header);
  });
});

describe("approvalBody", () => {
  test("is the flat shape AgentField reads, byte for byte what gets signed", () => {
    expect(approvalBody("apr-exec_first_run")).toBe(vector.body);
    expect(JSON.parse(approvalBody("apr-1"))).toEqual({ requestId: "apr-1", decision: "approved", feedback: "" });
  });
});
