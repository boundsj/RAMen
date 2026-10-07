// Evaluate a `.pragma library` QML JavaScript file under node and return its
// globals. QML's `.import "Other.js" as Other` lines become `var Other = {...}`
// so modules can depend on each other the same way they do in QML.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function load(name, cache = {}) {
  if (cache[name]) return cache[name];
  const file = path.join(__dirname, "..", name);
  const context = {};
  vm.createContext(context);
  let source = fs.readFileSync(file, "utf8").replace(/^\.pragma library\s*$/m, "");
  source = source.replace(/^\.import\s+"([^"]+)"\s+as\s+(\w+)\s*$/gm, (_, dep, alias) => {
    context[alias] = load(dep, cache);
    return "";
  });
  vm.runInContext(source, context, { filename: file });
  cache[name] = context;
  return context;
}

module.exports = { load };

// Values built inside the vm context have that realm's prototypes; compare plain copies.
function plain(value) {
  return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
}

module.exports.plain = plain;
