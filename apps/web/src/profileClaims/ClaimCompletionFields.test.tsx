import { fireEvent, render, screen } from "@testing-library/react"
import { useState } from "react"
import { expect, it } from "vitest"
import { ClaimCompletionFields, emptyClaimCompletion, toClaimCompletion } from "./ClaimCompletionFields"

function Form() {
  const [draft, setDraft] = useState(emptyClaimCompletion)
  return <><ClaimCompletionFields value={draft} onChange={setDraft} /><output aria-label="测试草稿">{JSON.stringify(draft)}</output></>
}

it("keeps unknown facts nullable and collects guardian facts without inferred demographics or authority", () => {
  render(<Form />)
  expect(toClaimCompletion(emptyClaimCompletion())).toBeNull()
  const birth = screen.getByLabelText("出生日期（可暂缺）")
  expect(birth).toHaveAttribute("type", "date")
  expect(birth).toHaveValue("")
  fireEvent.click(screen.getByRole("button", { name: /添加监护人/ }))
  fireEvent.change(screen.getByLabelText("监护人1身份证"), { target: { value: "110105198001021234" } })
  expect(screen.getByLabelText("监护人1性别")).toHaveValue("")
  expect(screen.getByLabelText("监护人1出生日期")).toHaveValue("")
  expect(screen.getByLabelText("监护人1为主要监护人")).not.toBeChecked()
  fireEvent.change(screen.getByLabelText("监护人1姓名"), { target: { value: "测试监护人" } })
  fireEvent.change(screen.getByLabelText("监护人1手机号"), { target: { value: "13800000000" } })
  fireEvent.change(screen.getByLabelText("监护人1与本人关系"), { target: { value: "父亲" } })
  fireEvent.change(screen.getByLabelText("监护人1性别"), { target: { value: "male" } })
  fireEvent.change(screen.getByLabelText("监护人1出生日期"), { target: { value: "1980-01-02" } })
  const value = toClaimCompletion(JSON.parse(screen.getByLabelText("测试草稿").textContent!))
  expect(value).toEqual({ phone: null, sex: null, date_of_birth: null, issuing_country: null, guardians: [
    { full_name: "测试监护人", identity_number: "110105198001021234", phone: "13800000000", email: null, relationship: "父亲", sex: "male", date_of_birth: "1980-01-02", is_primary: false },
  ] })
})

it("bounds guardian collection at ten and permits removal without defaulting missing sex", () => {
  render(<Form />)
  for (let i = 0; i < 10; i += 1) fireEvent.click(screen.getByRole("button", { name: /添加监护人/ }))
  expect(screen.getAllByRole("group", { name: /^监护人 \d+$/ })).toHaveLength(10)
  expect(screen.getByRole("button", { name: /添加监护人/ })).toBeDisabled()
  expect(() => toClaimCompletion(JSON.parse(screen.getByLabelText("测试草稿").textContent!))).toThrow("请填写监护人性别")
  fireEvent.click(screen.getByRole("button", { name: "移除监护人 10" }))
  expect(screen.getByRole("button", { name: /添加监护人/ })).toBeEnabled()
})
