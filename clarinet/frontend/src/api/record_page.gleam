import api/models.{type Record}
import gleam/dynamic/decode
import gleam/option.{type Option, None}

pub type RecordPage {
  RecordPage(items: List(Record), next_cursor: Option(String))
}

pub fn decoder(
  record_decoder: decode.Decoder(Record),
) -> decode.Decoder(RecordPage) {
  use items <- decode.field("items", decode.list(record_decoder))
  use next_cursor <- decode.optional_field(
    "next_cursor",
    None,
    decode.optional(decode.string),
  )
  decode.success(RecordPage(items: items, next_cursor: next_cursor))
}
