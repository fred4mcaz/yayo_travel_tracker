/** Small form primitives, so every sheet looks and behaves the same. */

interface FieldProps {
  label: string;
  hint?: string;
  children: React.ReactNode;
  wide?: boolean;
}

export function Field({ label, hint, children, wide }: FieldProps) {
  return (
    <label className={`field${wide ? " field-wide" : ""}`}>
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}

export function Row({ children }: { children: React.ReactNode }) {
  return <div className="field-row">{children}</div>;
}

interface TextProps {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  type?: string;
  maxLength?: number;
  required?: boolean;
}

export function Text({ value, onChange, ...rest }: TextProps) {
  return (
    <input
      value={value}
      onChange={(e) => onChange(e.target.value)}
      spellCheck={false}
      {...rest}
    />
  );
}

export function TextArea({
  value,
  onChange,
  rows = 3,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  rows?: number;
  placeholder?: string;
}) {
  return (
    <textarea
      value={value}
      rows={rows}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

export function Select<T extends string>({
  value,
  onChange,
  options,
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: string }[];
}) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value as T)}>
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

/* A Money input used to live here. Cost is no longer typed by hand -- it only
   arrives from a confirmation email -- so the control was removed rather than
   left lying around. The column and its read-only display remain. */
