from .main import run

src = """
extern fn puts(s: *u8) i32;

pub fn main() i32 {
    let mut a = 1;
    while (true) {
        puts("in while");
        if (true) {
            return 1;
        }
    };
    return 0;
    return 1;
}
"""
def main() -> None:
    run(src)
