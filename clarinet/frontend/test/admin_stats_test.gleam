import api/admin as admin_api
import gleam/dict
import gleam/json
import gleam/list
import gleeunit
import gleeunit/should

pub fn main() {
  gleeunit.main()
}

pub fn admin_stats_decoder_workload_test() {
  let json_str =
    "{\"total_studies\":2,\"total_records\":5,\"total_users\":3,"
    <> "\"total_patients\":1,\"records_by_status\":{\"pending\":4,\"inwork\":1},"
    <> "\"available_pending\":3,"
    <> "\"workload_by_user\":["
    <> "{\"user_id\":\"u-1\",\"email\":\"a@x.org\",\"inwork\":1,\"pending\":2,"
    <> "\"blocked\":0,\"failed\":1,\"finished\":7,\"available\":4}]}"

  let assert Ok(stats) = json.parse(json_str, admin_api.admin_stats_decoder())

  should.equal(stats.available_pending, 3)
  should.equal(list.length(stats.workload_by_user), 1)
  let assert [w] = stats.workload_by_user
  should.equal(w.user_id, "u-1")
  should.equal(w.email, "a@x.org")
  should.equal(w.inwork, 1)
  should.equal(w.pending, 2)
  should.equal(w.blocked, 0)
  should.equal(w.failed, 1)
  should.equal(w.finished, 7)
  should.equal(w.available, 4)
  should.equal(dict.size(stats.records_by_status), 2)
}
