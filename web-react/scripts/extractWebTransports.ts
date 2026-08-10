/// <reference types="node" />

import { readdirSync, readFileSync } from "node:fs";
import { dirname, extname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

export interface ExtractedWebTransport {
  transport: "browser" | "http" | "socketio";
  name: string;
  category?: "browser_file_handles" | "downloaded_bytes" | "multipart_form_data" | "text_range_streams";
  body_fields?: string[];
}

const WEB_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const HELPERS_ROOT = join(WEB_ROOT, "src/helpers");

function sourceFilesBelow(root: string): string[] {
  const files: string[] = [];
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    const path = join(root, entry.name);
    if (entry.isDirectory()) files.push(...sourceFilesBelow(path));
    else if ([".ts", ".tsx"].includes(extname(entry.name)) && !entry.name.endsWith(".gen.ts")) {
      files.push(path);
    }
  }
  return files.sort();
}

function propertyName(name: ts.PropertyName | ts.BindingName | undefined): string | null {
  if (!name) return null;
  if (ts.isIdentifier(name) || ts.isStringLiteral(name) || ts.isNumericLiteral(name)) return name.text;
  return null;
}

export function extractFrontendWebTransports(root = HELPERS_ROOT): {
  json: ExtractedWebTransport[];
  non_json: ExtractedWebTransport[];
} {
  const json = new Map<string, ExtractedWebTransport>();
  const nonJson = new Map<string, ExtractedWebTransport>();
  let hasBrowserFiles = false;

  const sources = sourceFilesBelow(root).map((filename) => {
    const source = readFileSync(filename, "utf8");
    const sourceFile = ts.createSourceFile(
      filename,
      source,
      ts.ScriptTarget.Latest,
      true,
      filename.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
    );
    return { filename, sourceFile };
  });
  const finiteTypeDomains = new Map<string, string[]>();
  for (const { sourceFile } of sources) {
    for (const statement of sourceFile.statements) {
      if (!ts.isTypeAliasDeclaration(statement) || !ts.isUnionTypeNode(statement.type)) continue;
      const values = statement.type.types.map((member) =>
        ts.isLiteralTypeNode(member) && ts.isStringLiteral(member.literal)
          ? member.literal.text
          : null,
      );
      if (values.every((value): value is string => value !== null)) {
        finiteTypeDomains.set(statement.name.text, values);
      }
    }
  }

  const addJson = (method: string, path: string, bodyFields?: string[]) => {
    const cleanPath = path.split("?", 1)[0].replace(/\/{2,}/g, "/");
    if (!cleanPath.startsWith("/api/") || cleanPath.includes("<dynamic>")) return;
    const entry: ExtractedWebTransport = {
      transport: "http",
      name: `${method} ${cleanPath}`,
      ...(bodyFields === undefined ? {} : { body_fields: [...bodyFields].sort() }),
    };
    const key = `${entry.name}|${entry.body_fields?.join(",") ?? "-"}`;
    json.set(key, entry);
  };
  const addNonJson = (
    method: string,
    path: string,
    category: NonNullable<ExtractedWebTransport["category"]>,
  ) => {
    const cleanPath = path.split("?", 1)[0].replace(/\/{2,}/g, "/");
    if (!cleanPath.startsWith("/api/") || cleanPath.includes("<dynamic>")) return;
    const entry: ExtractedWebTransport = {
      transport: "http",
      name: `${method} ${cleanPath}`,
      category,
    };
    nonJson.set(entry.name, entry);
  };

  for (const { filename, sourceFile } of sources) {
    const relativeName = relative(root, filename).replaceAll("\\", "/");
    const variables = new Map<string, ts.VariableDeclaration[]>();
    const constants = new Map<string, ts.Expression>();

    const indexBindings = (node: ts.Node) => {
      if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name)) {
        const declarations = variables.get(node.name.text) ?? [];
        declarations.push(node);
        variables.set(node.name.text, declarations);
        if (node.initializer) constants.set(node.name.text, node.initializer);
      } else if (ts.isParameter(node) && ts.isIdentifier(node.name)) {
        if (node.type?.getText(sourceFile) === "File" || node.type?.getText(sourceFile) === "File[]") {
          hasBrowserFiles = true;
        }
      }
      ts.forEachChild(node, indexBindings);
    };
    indexBindings(sourceFile);

    const unwrap = (expression: ts.Expression): ts.Expression => {
      if (
        ts.isAsExpression(expression) ||
        ts.isSatisfiesExpression(expression) ||
        ts.isParenthesizedExpression(expression)
      ) {
        return unwrap(expression.expression);
      }
      return expression;
    };
    const nearestVariable = (
      name: string,
      position: number,
    ): ts.VariableDeclaration | undefined =>
      variables
        .get(name)
        ?.filter((declaration) => declaration.pos < position)
        .sort((left, right) => right.pos - left.pos)[0];
    const literalText = (expression: ts.Expression): string | null => {
      const value = unwrap(expression);
      if (ts.isStringLiteral(value) || ts.isNoSubstitutionTemplateLiteral(value)) return value.text;
      if (ts.isIdentifier(value)) {
        const initializer = constants.get(value.text);
        return initializer ? literalText(initializer) : null;
      }
      if (value.kind === ts.SyntaxKind.TrueKeyword) return "true";
      if (value.kind === ts.SyntaxKind.FalseKeyword) return "false";
      return null;
    };
    const renderTemplate = (template: ts.TemplateExpression): string => {
      let rendered = template.head.text;
      for (const span of template.templateSpans) {
        rendered += literalText(span.expression) ?? "<dynamic>";
        rendered += span.literal.text;
      }
      return rendered;
    };
    const isParameterInScope = (identifier: ts.Identifier): boolean => {
      for (let current: ts.Node | undefined = identifier.parent; current; current = current.parent) {
        if (
          ts.isFunctionDeclaration(current) ||
          ts.isFunctionExpression(current) ||
          ts.isArrowFunction(current) ||
          ts.isMethodDeclaration(current)
        ) {
          return current.parameters.some(
            (parameter) => ts.isIdentifier(parameter.name) && parameter.name.text === identifier.text,
          );
        }
      }
      return false;
    };
    const bodyFields = (expression: ts.Expression): string[] | undefined => {
      let value = unwrap(expression);
      if (ts.isCallExpression(value) && value.expression.getText(sourceFile) === "JSON.stringify") {
        if (!value.arguments[0]) return undefined;
        value = unwrap(value.arguments[0]);
      }
      if (ts.isStringLiteral(value) && value.text === "{}") return [];
      if (ts.isIdentifier(value)) {
        if (isParameterInScope(value)) return undefined;
        const declaration = nearestVariable(value.text, value.pos);
        if (!declaration?.initializer) return undefined;
        value = unwrap(declaration.initializer);
      }
      if (!ts.isObjectLiteralExpression(value)) return undefined;
      const names: string[] = [];
      for (const property of value.properties) {
        if (ts.isSpreadAssignment(property)) return undefined;
        const name = propertyName(property.name);
        if (name) names.push(name);
      }
      return names.sort();
    };
    const bodyFromOptions = (expression: ts.Expression | undefined): string[] | undefined => {
      if (!expression) return undefined;
      const options = unwrap(expression);
      if (!ts.isObjectLiteralExpression(options)) return undefined;
      const body = options.properties.find(
        (property): property is ts.PropertyAssignment =>
          ts.isPropertyAssignment(property) && propertyName(property.name) === "body",
      );
      return body ? bodyFields(body.initializer) : undefined;
    };
    const optionsBody = (expression: ts.Expression | undefined): ts.Expression | undefined => {
      if (!expression) return undefined;
      const options = unwrap(expression);
      if (!ts.isObjectLiteralExpression(options)) return undefined;
      const body = options.properties.find(
        (property): property is ts.PropertyAssignment =>
          ts.isPropertyAssignment(property) && propertyName(property.name) === "body",
      );
      return body?.initializer;
    };
    const isFormDataBody = (expression: ts.Expression | undefined): boolean => {
      const body = optionsBody(expression);
      if (!body) return false;
      const value = unwrap(body);
      if (!ts.isIdentifier(value)) return false;
      const initializer = nearestVariable(value.text, value.pos)?.initializer;
      return (
        initializer !== undefined &&
        ts.isNewExpression(initializer) &&
        initializer.expression.getText(sourceFile) === "FormData"
      );
    };
    const staticText = (expression: ts.Expression): string | null => {
      const value = unwrap(expression);
      if (ts.isTemplateExpression(value)) return renderTemplate(value);
      return literalText(value);
    };
    const methodFromOptions = (expression: ts.Expression | undefined): string => {
      if (!expression) return "GET";
      const options = unwrap(expression);
      if (!ts.isObjectLiteralExpression(options)) return "GET";
      const method = options.properties.find(
        (property): property is ts.PropertyAssignment =>
          ts.isPropertyAssignment(property) && propertyName(property.name) === "method",
      );
      return method ? (literalText(method.initializer) ?? "GET") : "GET";
    };
    const finiteValues = (expression: ts.Expression): string[] | null => {
      const value = unwrap(expression);
      if (!ts.isIdentifier(value)) return null;
      for (let current: ts.Node | undefined = value.parent; current; current = current.parent) {
        if (!ts.isFunctionLike(current)) continue;
        const parameter = current.parameters.find(
          (candidate) => ts.isIdentifier(candidate.name) && candidate.name.text === value.text,
        );
        if (!parameter?.type || !ts.isTypeReferenceNode(parameter.type)) return null;
        const typeName = ts.isIdentifier(parameter.type.typeName)
          ? parameter.type.typeName.text
          : null;
        return typeName ? (finiteTypeDomains.get(typeName) ?? null) : null;
      }
      return null;
    };
    const routesFromExpression = (expression: ts.Expression): string[] => {
      const value = unwrap(expression);
      if (ts.isStringLiteral(value) || ts.isNoSubstitutionTemplateLiteral(value)) {
        const start = value.text.indexOf("/api/");
        return start >= 0 ? [value.text.slice(start)] : [];
      }
      if (ts.isTemplateExpression(value)) {
        let rendered = [value.head.text];
        for (const span of value.templateSpans) {
          const nestedRoutes = routesFromExpression(span.expression);
          const queryFragment =
            ts.isIdentifier(span.expression) && span.expression.text === "qs" ? "" : null;
          const replacements = finiteValues(span.expression) ?? [
            literalText(span.expression) ??
              nestedRoutes[0] ??
              queryFragment ??
              "<dynamic>",
          ];
          rendered = rendered.flatMap((prefix) =>
            replacements.map((replacement) => `${prefix}${replacement}${span.literal.text}`),
          );
        }
        return [
          ...new Set(
            rendered.flatMap((route) => {
              const start = route.indexOf("/api/");
              return start >= 0 ? [route.slice(start)] : [];
            }),
          ),
        ];
      }
      if (ts.isCallExpression(value) && ts.isIdentifier(value.expression)) {
        const helper = value.expression.text;
        const path = value.arguments[1] ? literalText(value.arguments[1]) : null;
        if (!path) return helper === "logViewUrl" ? ["/api/admin/logs/view"] : [];
        if (helper === "url") {
          if (relativeName === "admin/adminApi.ts") return [`/api/admin/${path}`];
          if (relativeName === "wizard/wizardApi.ts") return [`/api/wizard/${path}`];
          if (relativeName === "update/updateApi.ts") return [`/api/update/${path}`];
          if (relativeName === "tuner/tunerApi.ts") return [`/api/tuner/${path}`];
        }
        if (helper === "endpoint" || helper === "buildSettingsUrl") return [`/api/${path}`];
        if (helper === "logViewUrl") return ["/api/admin/logs/view"];
      }
      return [];
    };
    const fileRoute = (call: ts.CallExpression): string | null => {
      const kind = call.arguments[0] ? literalText(call.arguments[0]) : null;
      const path = call.arguments[1] ? literalText(call.arguments[1]) : null;
      return kind && path ? `/api/files/${kind}/${path}` : null;
    };
    const addCommandPaths = (expression: ts.Expression) => {
      const value = unwrap(expression);
      if (ts.isConditionalExpression(value)) {
        addCommandPaths(value.whenTrue);
        addCommandPaths(value.whenFalse);
        return;
      }
      if (!ts.isArrayLiteralExpression(value)) return;
      const segments = value.elements.map((segment) => literalText(segment as ts.Expression) ?? "<>");
      addJson("POST", `/api/${segments.join("/")}`);
    };

    const visit = (node: ts.Node) => {
      if (ts.isCallExpression(node)) {
        const callee = node.expression.getText(sourceFile);
        if (callee === "fetch" && node.arguments[0]) {
          for (const route of routesFromExpression(node.arguments[0])) {
            const method = methodFromOptions(node.arguments[1]);
            if (route === "/api/admin/logs/view") addNonJson(method, route, "text_range_streams");
            else if (route.split("?", 1)[0].endsWith("/artifact")) {
              addNonJson(method, route, "downloaded_bytes");
            } else if (isFormDataBody(node.arguments[1])) {
              addNonJson(method, route, "multipart_form_data");
            } else addJson(method, route, bodyFromOptions(node.arguments[1]));
          }
        } else if (["read", "write", "postForm"].includes(callee)) {
          const route = fileRoute(node);
          if (route) {
            if (callee === "read") addJson("GET", route);
            else if (callee === "postForm") addNonJson("POST", route, "multipart_form_data");
            else addJson("POST", route, node.arguments[2] ? bodyFields(node.arguments[2]) : undefined);
          }
        } else if (callee === "post" || callee === "get") {
          const path = node.arguments[1] ? staticText(node.arguments[1]) : null;
          const prefixes: Record<string, string> = {
            "admin/adminApi.ts": "/api/admin/",
            "update/updateApi.ts": "/api/update/",
            "tuner/tunerApi.ts": "/api/tuner/",
          };
          const prefix = prefixes[relativeName];
          if (path && prefix) {
            addJson(
              callee === "post" ? "POST" : "GET",
              `${prefix}${path}`,
              callee === "post" && node.arguments[2] ? bodyFields(node.arguments[2]) : undefined,
            );
          } else if (callee === "post" && relativeName === "command.ts" && node.arguments[1]) {
            addCommandPaths(node.arguments[1]);
          }
        } else if (callee === "postModelAction" && node.arguments[0]) {
          const path = literalText(node.arguments[0]);
          if (path) addJson("POST", `/api/${path}`, node.arguments[1] ? bodyFields(node.arguments[1]) : undefined);
        } else if (
          ts.isPropertyAccessExpression(node.expression) &&
          node.expression.name.text === "on" &&
          node.arguments[0] &&
          literalText(node.arguments[0])?.startsWith("socket_")
        ) {
          const name = literalText(node.arguments[0])!;
          json.set(`socketio|${name}`, { transport: "socketio", name });
        } else if (callee === "url" && node.arguments[1]) {
          const path = literalText(node.arguments[1]);
          const prefixes: Record<string, string> = {
            "admin/adminApi.ts": "/api/admin/",
            "update/updateApi.ts": "/api/update/",
          };
          const prefix = prefixes[relativeName];
          if (prefix && path?.includes("download")) {
            addNonJson("GET", `${prefix}${path}`, "downloaded_bytes");
          }
        }
      }

      if (ts.isTemplateExpression(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
        const rendered = ts.isTemplateExpression(node) ? renderTemplate(node) : node.text;
        const start = rendered.indexOf("/api/");
        if (start >= 0) {
          const route = rendered.slice(start);
          if (/\/(?:download|export)(?:\?|$)/.test(route)) {
            addNonJson("GET", route, "downloaded_bytes");
          }
        }
      }
      ts.forEachChild(node, visit);
    };
    visit(sourceFile);

  }

  if (hasBrowserFiles) {
    nonJson.set("browser file handles", {
      transport: "browser",
      name: "File and FileSystemHandle objects",
      category: "browser_file_handles",
    });
  }

  const byName = (left: ExtractedWebTransport, right: ExtractedWebTransport) =>
    left.name.localeCompare(right.name) || JSON.stringify(left).localeCompare(JSON.stringify(right));
  return {
    json: [...json.values()].sort(byName),
    non_json: [...nonJson.values()].sort(byName),
  };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  if (!process.argv.includes("--json")) throw new Error("Usage: bun extractWebTransports.ts --json");
  process.stdout.write(`${JSON.stringify(extractFrontendWebTransports(), null, 2)}\n`);
}
