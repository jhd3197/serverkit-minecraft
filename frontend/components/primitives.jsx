// Local stand-ins for the host components these pages need that are NOT on
// the serverkit-sdk surface (Button, Input, Checkbox, Select, Card, FormField,
// Spinner). They render the same host design-system classes (.btn-*,
// .ui-input, .ui-checkbox, .ui-select-*, .ui-card*, .form-field*, .spinner*)
// the core components emit — Select and Checkbox are the host's own
// components with the deep `@/` imports swapped for bundled Radix deps, so
// behavior and styling match exactly. A runtime-ESM bundle cannot import
// host internals, hence the copies.
import * as React from 'react';
import * as CheckboxPrimitive from '@radix-ui/react-checkbox';
import * as SelectPrimitive from '@radix-ui/react-select';
import { Check, ChevronDown, ChevronUp } from 'lucide-react';

// Minimal cn() — the host's is clsx+tailwind-merge; every call site here
// passes plain strings, so a truthy-join is equivalent.
const cn = (...parts) => parts.filter(Boolean).join(' ');

// Mirrors frontend/src/components/ui/button.jsx's variant/size -> class map.
const VARIANT_CLASSES = {
    default: 'btn-primary',
    primary: 'btn-primary',
    destructive: 'btn-danger',
    danger: 'btn-danger',
    outline: 'btn-secondary',
    secondary: 'btn-soft',
    ghost: 'btn-ghost',
    link: 'btn-link',
};
const SIZE_CLASSES = { sm: 'btn-sm', lg: 'btn-lg', icon: 'btn-icon' };

export function Button({ variant = 'default', size, className = '', children, type, ...props }) {
    const classes = [
        'btn',
        VARIANT_CLASSES[variant] || 'btn-primary',
        SIZE_CLASSES[size] || '',
        className,
    ].filter(Boolean).join(' ');
    return (
        <button type={type || 'button'} className={classes} {...props}>
            {children}
        </button>
    );
}

// Mirrors frontend/src/components/ui/input.jsx (class .ui-input).
export function Input({ className = '', type, ...props }) {
    return (
        <input
            type={type}
            data-slot="input"
            className={cn('ui-input', className)}
            {...props}
        />
    );
}

// Mirrors frontend/src/components/ui/label.jsx.
export function Label({ className = '', children, ...props }) {
    return (
        <label data-slot="label" className={cn('ui-label', className)} {...props}>
            {children}
        </label>
    );
}

// Host copy: frontend/src/components/ui/checkbox.jsx (Radix bundled).
export const Checkbox = React.forwardRef(({ className, ...props }, ref) => (
    <CheckboxPrimitive.Root
        ref={ref}
        data-slot="checkbox"
        className={cn('peer ui-checkbox', className)}
        {...props}
    >
        <CheckboxPrimitive.Indicator>
            <Check />
        </CheckboxPrimitive.Indicator>
    </CheckboxPrimitive.Root>
));
Checkbox.displayName = CheckboxPrimitive.Root.displayName;

// Host copy: frontend/src/components/ui/select.jsx (Radix bundled).
export const Select = SelectPrimitive.Root;
export const SelectValue = SelectPrimitive.Value;

export const SelectTrigger = React.forwardRef(({ className, children, ...props }, ref) => (
    <SelectPrimitive.Trigger
        ref={ref}
        className={cn('ui-select-trigger', className)}
        {...props}
    >
        {children}
        <SelectPrimitive.Icon asChild>
            <ChevronDown className="ui-select-icon" />
        </SelectPrimitive.Icon>
    </SelectPrimitive.Trigger>
));
SelectTrigger.displayName = SelectPrimitive.Trigger.displayName;

const SelectScrollUpButton = React.forwardRef(({ className, ...props }, ref) => (
    <SelectPrimitive.ScrollUpButton
        ref={ref}
        className={cn('ui-select-scroll-button', className)}
        {...props}
    >
        <ChevronUp size={16} />
    </SelectPrimitive.ScrollUpButton>
));
SelectScrollUpButton.displayName = SelectPrimitive.ScrollUpButton.displayName;

const SelectScrollDownButton = React.forwardRef(({ className, ...props }, ref) => (
    <SelectPrimitive.ScrollDownButton
        ref={ref}
        className={cn('ui-select-scroll-button', className)}
        {...props}
    >
        <ChevronDown size={16} />
    </SelectPrimitive.ScrollDownButton>
));
SelectScrollDownButton.displayName = SelectPrimitive.ScrollDownButton.displayName;

export const SelectContent = React.forwardRef(({ className, children, position = 'popper', ...props }, ref) => (
    <SelectPrimitive.Portal>
        <SelectPrimitive.Content
            ref={ref}
            className={cn('ui-select-content', className)}
            position={position}
            {...props}
        >
            <SelectScrollUpButton />
            <SelectPrimitive.Viewport className="ui-select-viewport">
                {children}
            </SelectPrimitive.Viewport>
            <SelectScrollDownButton />
        </SelectPrimitive.Content>
    </SelectPrimitive.Portal>
));
SelectContent.displayName = SelectPrimitive.Content.displayName;

export const SelectItem = React.forwardRef(({ className, children, ...props }, ref) => (
    <SelectPrimitive.Item
        ref={ref}
        className={cn('ui-select-item', className)}
        {...props}
    >
        <span className="ui-select-item-indicator">
            <SelectPrimitive.ItemIndicator>
                <Check />
            </SelectPrimitive.ItemIndicator>
        </span>
        <SelectPrimitive.ItemText>{children}</SelectPrimitive.ItemText>
    </SelectPrimitive.Item>
));
SelectItem.displayName = SelectPrimitive.Item.displayName;

// Host copy: frontend/src/components/ui/card.jsx (plain class wrappers).
export function Card({ className, ...props }) {
    return <div data-slot="card" className={cn('ui-card', className)} {...props} />;
}

export function CardHeader({ className, ...props }) {
    return <div data-slot="card-header" className={cn('ui-card-header', className)} {...props} />;
}

export function CardTitle({ className, ...props }) {
    return <div data-slot="card-title" className={cn('ui-card-title', className)} {...props} />;
}

export function CardContent({ className, ...props }) {
    return <div data-slot="card-content" className={cn('ui-card-content', className)} {...props} />;
}

// Host copy: frontend/src/components/FormField.jsx — label, hint, error and
// input slot, rendering the shared .form-field classes.
export function FormField({ label, htmlFor, children, error, hint, required = false, className }) {
    return (
        <div className={cn('form-field', className)}>
            {label && (
                <Label htmlFor={htmlFor} className="form-field__label">
                    {label}
                    {required && <span className="form-field__required" aria-hidden="true">*</span>}
                </Label>
            )}
            <div className="form-field__control">
                {children}
            </div>
            {hint && !error && <p className="form-field__hint">{hint}</p>}
            {error && <p className="form-field__error" role="alert">{error}</p>}
        </div>
    );
}

export function FormRow({ children, className }) {
    return (
        <div className={cn('form-row', className)}>
            {children}
        </div>
    );
}

// Host copy: frontend/src/components/Spinner.jsx markup.
export function Spinner({ size = 'md', className = '' }) {
    const sizeClasses = { sm: 'spinner-sm', md: 'spinner-md', lg: 'spinner-lg' };
    return (
        <div className={`spinner ${sizeClasses[size] || 'spinner-md'} ${className}`}>
            <div className="spinner-ring" />
        </div>
    );
}
