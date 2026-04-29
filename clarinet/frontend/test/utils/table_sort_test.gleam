// Unit tests for utils/table_sort
import gleam/dict
import gleam/int
import gleam/order
import gleeunit/should
import utils/table_sort.{Asc, Desc}

// --- parse_sort_dir / sort_dir_to_string ---

pub fn parse_sort_dir_desc_test() {
  table_sort.parse_sort_dir("desc") |> should.equal(Desc)
}

pub fn parse_sort_dir_asc_test() {
  table_sort.parse_sort_dir("asc") |> should.equal(Asc)
}

pub fn parse_sort_dir_unknown_test() {
  table_sort.parse_sort_dir("ascending") |> should.equal(Asc)
  table_sort.parse_sort_dir("") |> should.equal(Asc)
  table_sort.parse_sort_dir("DESC") |> should.equal(Asc)
}

pub fn sort_dir_roundtrip_test() {
  table_sort.parse_sort_dir(table_sort.sort_dir_to_string(Asc))
  |> should.equal(Asc)
  table_sort.parse_sort_dir(table_sort.sort_dir_to_string(Desc))
  |> should.equal(Desc)
}

// --- toggle_dir ---

pub fn toggle_dir_test() {
  table_sort.toggle_dir(Asc) |> should.equal(Desc)
  table_sort.toggle_dir(Desc) |> should.equal(Asc)
}

// --- read_sort ---

pub fn read_sort_empty_returns_default_asc_test() {
  table_sort.read_sort(dict.new(), "id")
  |> should.equal(#("id", Asc))
}

pub fn read_sort_only_dir_returns_default_col_test() {
  dict.from_list([#("sort_dir", "desc")])
  |> table_sort.read_sort("id")
  |> should.equal(#("id", Desc))
}

pub fn read_sort_only_col_returns_asc_test() {
  dict.from_list([#("sort", "name")])
  |> table_sort.read_sort("id")
  |> should.equal(#("name", Asc))
}

pub fn read_sort_full_test() {
  dict.from_list([#("sort", "date"), #("sort_dir", "desc")])
  |> table_sort.read_sort("id")
  |> should.equal(#("date", Desc))
}

// --- next_sort ---

pub fn next_sort_same_col_toggles_test() {
  table_sort.next_sort("id", Asc, "id") |> should.equal(#("id", Desc))
  table_sort.next_sort("id", Desc, "id") |> should.equal(#("id", Asc))
}

pub fn next_sort_different_col_resets_to_asc_test() {
  table_sort.next_sort("id", Asc, "name") |> should.equal(#("name", Asc))
  table_sort.next_sort("id", Desc, "name") |> should.equal(#("name", Asc))
}

// --- write_sort ---

pub fn write_sort_inserts_keys_test() {
  let result = table_sort.write_sort(dict.new(), "name", Desc, "id")
  dict.get(result, "sort") |> should.equal(Ok("name"))
  dict.get(result, "sort_dir") |> should.equal(Ok("desc"))
}

pub fn write_sort_default_col_asc_strips_keys_test() {
  // sort=id+asc is the default — it should disappear from the URL.
  let initial = dict.from_list([#("sort", "name"), #("sort_dir", "desc")])
  let result = table_sort.write_sort(initial, "id", Asc, "id")
  dict.get(result, "sort") |> should.equal(Error(Nil))
  dict.get(result, "sort_dir") |> should.equal(Error(Nil))
}

pub fn write_sort_default_col_desc_keeps_keys_test() {
  // Default column with non-default direction is still meaningful.
  let result = table_sort.write_sort(dict.new(), "id", Desc, "id")
  dict.get(result, "sort") |> should.equal(Ok("id"))
  dict.get(result, "sort_dir") |> should.equal(Ok("desc"))
}

pub fn write_sort_preserves_other_keys_test() {
  let initial = dict.from_list([#("status", "pending")])
  let result = table_sort.write_sort(initial, "name", Asc, "id")
  dict.get(result, "status") |> should.equal(Ok("pending"))
  dict.get(result, "sort") |> should.equal(Ok("name"))
}

// --- read_sort / write_sort roundtrip ---

pub fn read_write_roundtrip_test() {
  let written = table_sort.write_sort(dict.new(), "date", Desc, "id")
  table_sort.read_sort(written, "id") |> should.equal(#("date", Desc))
}

// --- with_direction ---

pub fn with_direction_asc_passthrough_test() {
  let cmp = table_sort.with_direction(int.compare, Asc)
  cmp(1, 2) |> should.equal(order.Lt)
  cmp(2, 1) |> should.equal(order.Gt)
  cmp(1, 1) |> should.equal(order.Eq)
}

pub fn with_direction_desc_inverts_test() {
  let cmp = table_sort.with_direction(int.compare, Desc)
  cmp(1, 2) |> should.equal(order.Gt)
  cmp(2, 1) |> should.equal(order.Lt)
  cmp(1, 1) |> should.equal(order.Eq)
}
