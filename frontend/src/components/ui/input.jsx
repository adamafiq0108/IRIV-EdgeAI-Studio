import * as React from "react"
import { Input as InputPrimitive } from "@base-ui/react/input"

import { Eye, EyeOff } from "lucide-react"

import { cn } from "@/lib/utils"

function Input({
  className,
  type,
  ...props
}) {
  return (
    <InputPrimitive
      type={type}
      data-slot="input"
      className={cn(
        "h-8 w-full min-w-0 rounded-lg border border-input bg-transparent px-2.5 py-1 text-base transition-colors outline-none file:inline-flex file:h-6 file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-foreground placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:pointer-events-none disabled:cursor-not-allowed disabled:bg-input/50 disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 md:text-sm dark:bg-input/30 dark:disabled:bg-input/80 dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40",
        className
      )}
      {...props} />
  );
}

/** A password box with a reveal toggle -- the ONE every password field uses.
 *
 *  A password you cannot read is a password you type three times and still get
 *  wrong - and the box that rejects it will not say which character was
 *  missed. The risk being defended against is somebody ELSE reading the
 *  screen, so revealing is deliberate, momentary, and never the default: it
 *  starts masked and every caller that reopens a form resets it.
 *
 *  tabIndex={-1} on the toggle keeps Tab going from the field to the submit
 *  button, which is the path somebody typing a password is on.
 *
 *  It reveals only what the FIELD holds -- what was typed, or what the page
 *  was given. It never asks the server for a stored secret: a field that
 *  shows "unchanged" for a kept password reveals an empty box.
 *
 *  `shown`/`onToggle` are optional: left out, the field keeps its own state
 *  (pass them to make two fields -- a password and its repeat -- reveal
 *  together). `native` draws a plain <input> with the caller's className
 *  (the app's `.input` and panel styles) inside the .pw-wrap the sign-in
 *  pages already use; without it, the design-system Input. Every other prop
 *  -- autoComplete, name, required, placeholder -- goes to the input. */
function PasswordInput({ className, shown, onToggle, native, ...props }) {
  const [own, setOwn] = React.useState(false);
  const isShown = shown ?? own;
  const toggle = onToggle ?? (() => setOwn((v) => !v));
  const label = isShown ? "Hide password" : "Show password";
  if (native) {
    return (
      <span className="pw-wrap" style={{ display: "block" }}>
        <input {...props} className={className}
               type={isShown ? "text" : "password"} />
        <button type="button" className="pw-toggle" tabIndex={-1}
                aria-label={label} title={label} aria-pressed={isShown}
                onClick={toggle}>
          {isShown ? <EyeOff size={16} /> : <Eye size={16} />}
        </button>
      </span>
    );
  }
  return (
    <div className="relative">
      <Input {...props} type={isShown ? "text" : "password"}
             className={cn("pr-9", className)} />
      <button type="button" tabIndex={-1}
              aria-label={label} title={isShown ? "Hide" : "Show"}
              aria-pressed={isShown}
              onClick={toggle}
              className="absolute inset-y-0 right-0 flex w-9 items-center
                         justify-center text-muted-foreground hover:text-foreground">
        {isShown ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
      </button>
    </div>
  );
}

export { Input, PasswordInput }
