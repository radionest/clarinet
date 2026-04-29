// Shared utilities for sortable HTML tables driven by URL filters.
//
// Sort state lives in two filter keys: "sort" (column id) and "sort_dir"
// ("asc"/"desc"). Pages embed the keys into their existing
// Dict(String, String) filter map; the router whitelist already accepts
// them, so no extra parsing logic is needed.
import gleam/dict.{type Dict}
import gleam/order
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html
import lustre/event

pub type SortDirection {
  Asc
  Desc
}

pub fn parse_sort_dir(s: String) -> SortDirection {
  case s {
    "desc" -> Desc
    _ -> Asc
  }
}

pub fn sort_dir_to_string(d: SortDirection) -> String {
  case d {
    Asc -> "asc"
    Desc -> "desc"
  }
}

pub fn toggle_dir(d: SortDirection) -> SortDirection {
  case d {
    Asc -> Desc
    Desc -> Asc
  }
}

pub fn read_sort(
  filters: Dict(String, String),
  default_col: String,
) -> #(String, SortDirection) {
  let col = case dict.get(filters, "sort") {
    Ok(c) -> c
    Error(_) -> default_col
  }
  let dir = case dict.get(filters, "sort_dir") {
    Ok(d) -> parse_sort_dir(d)
    Error(_) -> Asc
  }
  #(col, dir)
}

pub fn next_sort(
  current_col: String,
  current_dir: SortDirection,
  clicked_col: String,
) -> #(String, SortDirection) {
  case clicked_col == current_col {
    True -> #(current_col, toggle_dir(current_dir))
    False -> #(clicked_col, Asc)
  }
}

pub fn write_sort(
  filters: Dict(String, String),
  col: String,
  dir: SortDirection,
) -> Dict(String, String) {
  filters
  |> dict.insert("sort", col)
  |> dict.insert("sort_dir", sort_dir_to_string(dir))
}

pub fn th_sortable(
  label: String,
  key: String,
  current_col: String,
  current_dir: SortDirection,
  on_click_for: fn(String) -> msg,
) -> Element(msg) {
  let is_active = key == current_col
  let arrow = case is_active, current_dir {
    True, Asc -> " \u{2191}"
    True, Desc -> " \u{2193}"
    False, _ -> ""
  }
  let aria_sort = case is_active, current_dir {
    True, Asc -> "ascending"
    True, Desc -> "descending"
    False, _ -> "none"
  }
  let cls = case is_active {
    True -> "sortable sortable-active"
    False -> "sortable"
  }
  // The clickable element is a <button> inside <th> so it is keyboard-
  // focusable and screen readers announce it as a control.
  html.th(
    [attribute.class(cls), attribute.attribute("aria-sort", aria_sort)],
    [
      html.button(
        [
          attribute.type_("button"),
          attribute.class("table-sort-trigger"),
          event.on_click(on_click_for(key)),
        ],
        [html.text(label <> arrow)],
      ),
    ],
  )
}

pub fn th_static(label: String) -> Element(msg) {
  html.th([], [html.text(label)])
}

/// Wrap a base comparator with the requested sort direction.
/// `Asc` returns the comparator unchanged; `Desc` negates the result.
pub fn with_direction(
  base: fn(a, a) -> order.Order,
  dir: SortDirection,
) -> fn(a, a) -> order.Order {
  case dir {
    Asc -> base
    Desc -> fn(x, y) { order.negate(base(x, y)) }
  }
}
