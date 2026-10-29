// Universal search: route parsing/serialization and page rendering.
// Views are rendered to HTML; effects never run.
import gleam/option.{Some}
import gleam/uri
import gleeunit/should
import router

pub fn parse_search_route_test() {
  let assert Ok(u) = uri.parse("/search?q=CLARINET_12")
  router.parse_route(u) |> should.equal(router.Search("CLARINET_12"))
}

pub fn parse_search_route_without_query_test() {
  let assert Ok(u) = uri.parse("/search")
  router.parse_route(u) |> should.equal(router.Search(""))
}

pub fn search_route_requires_admin_test() {
  router.requires_admin_role(router.Search("x")) |> should.be_true
}

pub fn search_route_to_query_encodes_test() {
  router.route_to_query(router.Search("a b"))
  |> should.equal(Some("q=a%20b"))
}

pub fn search_route_path_test() {
  router.route_to_path(router.Search("x")) |> should.equal("/search")
}
