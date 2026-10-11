.pragma library

// Qt cursor offsets and field limits use UTF-16 units. Reject whole pastes,
// rather than silently changing a folder path or a search expression.
function valid(text, limit) {
  return typeof text === "string" && text.length <= limit && !/[\x00-\x1f\x7f\u2028\u2029]/.test(text)
}
function replace(text, start, end, value, limit) {
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end < start || end > text.length
      || !valid(value, limit)) return null
  var out = text.slice(0, start) + value + text.slice(end)
  if (!valid(out, limit)) return null
  return { text: out, cursor: start + value.length }
}
