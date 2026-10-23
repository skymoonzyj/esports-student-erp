import { Eye, EyeOff } from "lucide-react"
import { useState } from "react"
import type { ComponentPropsWithoutRef } from "react"

import "./PasswordInput.css"

type PasswordInputProps = Omit<ComponentPropsWithoutRef<"input">, "type">

export function PasswordInput({ className, ...inputProps }: PasswordInputProps) {
  const [visible, setVisible] = useState(false)
  const label = visible ? "隐藏密码" : "显示密码"

  return (
    <div className="passwordInput">
      <input {...inputProps} className={className} type={visible ? "text" : "password"} />
      <button
        className="passwordVisibilityToggle"
        type="button"
        aria-label={label}
        aria-pressed={visible}
        title={label}
        onClick={() => setVisible((current) => !current)}
      >
        {visible ? <EyeOff aria-hidden="true" /> : <Eye aria-hidden="true" />}
      </button>
    </div>
  )
}
