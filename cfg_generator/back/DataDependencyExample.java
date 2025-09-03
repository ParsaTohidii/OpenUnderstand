public class DataDependencyExample {


    public static int methodOne(int x, int y) {
        int a = x + 5;
        int b = y * 2;
        int c = a - b;
        int d = c + 3;

        if (a > b) {
            d--;
        } else {
            d++;
        }

        b = b * 3;
        c = c + d;

        return c;
    }


    public static int methodTwo(int p, int q) {
        int m = p + 7;
        int n = q - 4;
        int o = m * n;
        int p2 = o / 2;

        if (n < 0) {
            p2++;
        }

        m = m + p2;
        n = n - p2;

        return m;
    }


    public static int methodThree(int i, int j) {
        int x = i * 2;
        int y = j - 1;
        int z = x + y;

        if (x > y) {
            z++;
        }

        int p = z * 4;
        int q = p + 5;
        int r = q / 2;

        return r;
    }

}
