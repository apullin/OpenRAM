// Spice netlist model + serializer: a byte-exact port of
// hierarchy_spice.sp_write/sp_write_file. This is also the pilot data
// model for moving netlist ownership into Rust: modules/instances/
// connections live here as plain data, and the writer reproduces the
// reference post-order traversal with name-based deduplication.

use std::collections::HashSet;

#[derive(Default)]
pub struct NlModule {
    pub name: String,
    pub cell_name: String,
    pub no_instances: bool,
    /// Library cells carry their spice text verbatim.
    pub spice_text: Option<String>,
    pub lvs_text: Option<String>,
    /// (name, type) in pin order.
    pub pins: Vec<(String, String)>,
    pub comments: Vec<String>,
    pub spice_device: Option<String>,
    pub lvs_device: Option<String>,
    /// Child module ids, pre-sorted like sp_write_file iterates them.
    pub children: Vec<usize>,
    pub insts: Vec<NlInst>,
    pub trim_insts: HashSet<String>,
}

pub struct NlInst {
    pub name: String,
    pub module: usize,
    pub conns: Vec<String>,
    pub has_pins: bool,
}

#[derive(Default)]
pub struct NetlistDb {
    pub modules: Vec<NlModule>,
    pub top: Option<usize>,
}

/// textwrap.wrap(text, 70) for whitespace-separated tokens (greedy fill).
fn wrap70(text: &str) -> Vec<String> {
    let mut lines: Vec<String> = Vec::new();
    let mut cur = String::new();
    for word in text.split_whitespace() {
        if cur.is_empty() {
            cur.push_str(word);
        } else if cur.len() + 1 + word.len() <= 70 {
            cur.push(' ');
            cur.push_str(word);
        } else {
            lines.push(std::mem::take(&mut cur));
            cur.push_str(word);
        }
    }
    if !cur.is_empty() {
        lines.push(cur);
    }
    lines
}

/// hierarchy_spice._wrap_spice_line.
fn wrap_line(text: &str, sep: &str) -> String {
    if text.len() <= 70 && !text.contains("  ") && text == text.trim() {
        return text.to_string();
    }
    wrap70(text).join(sep)
}

fn format_device(template: &str, name: &str, conns: &str) -> String {
    template.replace("{0}", name).replace("{1}", conns)
}

impl NetlistDb {
    fn write_module(&self, id: usize, out: &mut String, used: &mut Vec<String>, lvs: bool, trim: bool) {
        let m = &self.modules[id];
        if m.no_instances {
            return;
        }
        if m.spice_text.is_none() {
            for &child in &m.children {
                let cname = &self.modules[child].name;
                if used.iter().any(|n| n == cname) {
                    continue;
                }
                used.push(cname.clone());
                self.write_module(child, out, used, lvs, trim);
            }
            if m.insts.is_empty() || m.pins.is_empty() {
                return;
            }
            let pin_names: Vec<&str> = m.pins.iter().map(|(n, _)| n.as_str()).collect();
            let wrapped_pins = wrap_line(&pin_names.join(" "), "\n+ ");
            out.push_str(&format!("\n.SUBCKT {0}\n+ {1}\n", m.cell_name, wrapped_pins));
            for (name, ptype) in &m.pins {
                out.push_str(&format!("* {1:<6}: {0} \n", name, ptype));
            }
            for line in &m.comments {
                out.push_str(&format!("* {}\n", line));
            }
            for inst in &m.insts {
                if !inst.has_pins {
                    continue;
                }
                let child = &self.modules[inst.module];
                if child.no_instances {
                    continue;
                }
                let trimmed = trim && m.trim_insts.contains(&inst.name);
                if trimmed {
                    out.push_str("* ");
                }
                let conns = inst.conns.join(" ");
                if lvs && child.lvs_device.is_some() {
                    out.push_str(&format_device(child.lvs_device.as_ref().unwrap(), &inst.name, &conns));
                    out.push('\n');
                } else if child.spice_device.is_some() {
                    out.push_str(&format_device(child.spice_device.as_ref().unwrap(), &inst.name, &conns));
                    out.push('\n');
                } else if trimmed {
                    let wrapped = wrap_line(&conns, "\n*+ ");
                    out.push_str(&format!("X{0}\n*+ {1}\n*+ {2}\n", inst.name, wrapped, child.cell_name));
                } else {
                    let wrapped = wrap_line(&conns, "\n+ ");
                    out.push_str(&format!("X{0}\n+ {1}\n+ {2}\n", inst.name, wrapped, child.cell_name));
                }
            }
            out.push_str(&format!(".ENDS {0}\n", m.cell_name));
        } else {
            if lvs && m.lvs_text.is_some() {
                out.push_str(m.lvs_text.as_ref().unwrap());
            } else {
                out.push_str(m.spice_text.as_ref().unwrap());
            }
            out.push('\n');
        }
    }

    /// sp_write: full file contents.
    pub fn write_spice(&self, lvs: bool, trim: bool) -> String {
        let mut out = String::with_capacity(1 << 20);
        out.push_str("*FIRST LINE IS A COMMENT\n");
        if let Some(top) = self.top {
            let mut used: Vec<String> = Vec::new();
            self.write_module(top, &mut out, &mut used, lvs, trim);
        }
        out
    }
}
