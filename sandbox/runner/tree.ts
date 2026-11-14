export type TreeLine = {
  id: number;
  depth: number;
  interactive: boolean;
  role: string | null;
  name: string | null;
  value: string | null;
  text: string | null;
};

export type SemanticNode = {
  nodeId?: string;
  backendDOMNodeId: number;
  nodeName: string;
  xpath: string;
  nodeType: number;
  isInteractive?: boolean;
  role?: string;
  name?: string;
  value?: string;
  nodeValue?: string;
  attributes?: Record<string, string>;
  children?: SemanticNode[];
};

export type PageElement = {
  ref: number;
  role: string;
  name: string;
  value?: string;
  form: string | null;
  tag: string;
  fieldName?: string;
  inputType?: string;
};

export type Table = { columns: string[]; rows: string[][] };

const treeLine = /^( *)(\d+) (\[i\] )?(.*)$/;
const quotedWithValue = /^'(.*)' value='(.*)'$/;
const quoted = /^'(.*)'(.*)$/;
const valueOnly = /^value='(.*)'$/;

function lineOf(raw: string, number: number): TreeLine {
  const match = treeLine.exec(raw);
  if (!match) throw new Error(`Semantic tree line ${number} is not a tree line: ${raw.slice(0, 80)}`);
  const [, indent, id, interactiveMark, rest] = match;
  const line: TreeLine = {
    id: Number(id),
    depth: indent.length,
    interactive: interactiveMark !== undefined,
    role: null,
    name: null,
    value: null,
    text: null,
  };
  if (rest.startsWith("'")) return { ...line, text: quoted.exec(rest)?.[1] ?? rest.slice(1) };
  const space = rest.indexOf(" ");
  const role = space === -1 ? rest : rest.slice(0, space);
  const after = space === -1 ? "" : rest.slice(space + 1);
  const withValue = quotedWithValue.exec(after);
  if (withValue) return { ...line, role, name: withValue[1], value: withValue[2] };
  const named = quoted.exec(after);
  if (named) return { ...line, role, name: named[1] };
  return { ...line, role, value: valueOnly.exec(after)?.[1] ?? null };
}

export function parseTextTree(text: string): TreeLine[] {
  return text
    .split("\n")
    .map((raw, index) => ({ raw, number: index + 1 }))
    .filter(({ raw }) => raw.trim() !== "")
    .map(({ raw, number }) => lineOf(raw, number));
}

export function textOf(lines: readonly TreeLine[]) {
  const texts = lines.flatMap((line) => (line.text === null ? [] : [line.text]));
  const parts = texts.length > 0 ? texts : lines.flatMap((line) => (line.name === null ? [] : [line.name]));
  return normalize(parts.join(" "));
}

function normalize(text: string) {
  return text.replace(/\s+/g, " ").trim();
}

function formKeyOf(form: SemanticNode) {
  const action = form.attributes?.action?.trim();
  return action ? action : form.xpath;
}

function formFromXpath(xpath: string) {
  const match = /^(.*\/form\[\d+\])\//.exec(xpath);
  return match ? match[1] : null;
}

function elementOf(node: SemanticNode, form: string | null): PageElement {
  const fieldName = node.attributes?.name;
  return {
    ref: node.backendDOMNodeId,
    role: node.role ?? node.nodeName,
    name: node.name ?? "",
    ...(node.value === undefined ? {} : { value: node.value }),
    form,
    tag: node.nodeName,
    ...(fieldName === undefined ? {} : { fieldName }),
    ...(node.nodeName === "input" ? { inputType: (node.attributes?.type ?? "text").toLowerCase() } : {}),
  };
}

export function elementsOf(root: SemanticNode): PageElement[] {
  const elements: PageElement[] = [];
  const visit = (node: SemanticNode, form: string | null) => {
    const here = node.nodeName === "form" ? formKeyOf(node) : form;
    if (node.isInteractive) elements.push(elementOf(node, here ?? formFromXpath(node.xpath)));
    for (const child of node.children ?? []) visit(child, here);
  };
  visit(root, null);
  return elements;
}

const cellPlace = /^(.*\/table\[\d+\])\/(?:(thead|tbody|tfoot)\[\d+\]\/)?tr\[(\d+)\]\/(td|th)\[(\d+)\]/;

type RowDraft = { cells: Map<number, string[]>; hasData: boolean };
type TableDraft = { headers: Map<number, string>; rows: Map<string, RowDraft> };

function contributionOf(node: SemanticNode) {
  if (node.nodeType === 3) return node.nodeValue;
  if (node.isInteractive) return node.value ?? node.name;
  return undefined;
}

function joined(parts: readonly string[] | undefined) {
  return normalize((parts ?? []).join(" "));
}

export function tablesOf(root: SemanticNode): Table[] {
  const tables = new Map<string, TableDraft>();
  const visit = (node: SemanticNode) => {
    const place = cellPlace.exec(node.xpath);
    if (place) {
      const [, tableKey, section = "tbody", row, kind, column] = place;
      const columnIndex = Number(column);
      const table = tables.get(tableKey) ?? { headers: new Map(), rows: new Map() };
      tables.set(tableKey, table);
      if (node.role === "columnheader" && node.name) table.headers.set(columnIndex, node.name);
      const rowKey = `${section}:${row}`;
      const draft = table.rows.get(rowKey) ?? { cells: new Map(), hasData: false };
      table.rows.set(rowKey, draft);
      if (kind === "td") draft.hasData = true;
      const contribution = contributionOf(node);
      if (contribution !== undefined && contribution.trim() !== "") {
        draft.cells.set(columnIndex, [...(draft.cells.get(columnIndex) ?? []), contribution]);
      }
    }
    for (const child of node.children ?? []) visit(child);
  };
  visit(root);

  return [...tables.values()].flatMap((table) => {
    const rows = [...table.rows.values()];
    const dataRows = rows.filter((row) => row.hasData);
    const headerRow = rows.find((row) => !row.hasData);
    const width = Math.max(0, ...table.headers.keys(), ...rows.flatMap((row) => [...row.cells.keys()]));
    if (width === 0) return [];
    const columns = Array.from({ length: width }, (_, index) => table.headers.get(index + 1) ?? joined(headerRow?.cells.get(index + 1)));
    return [{ columns, rows: dataRows.map((row) => Array.from({ length: width }, (_, index) => joined(row.cells.get(index + 1)))) }];
  });
}
