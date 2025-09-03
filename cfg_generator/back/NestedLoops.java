public class NestedLoops {

    public static void main(String[] args) {
        NestedLoops loops = new NestedLoops();
        int result = loops.computeValues(5, 10);
        System.out.println("Computed Value: " + result);
    }

    public int computeValues(int a, int b) {
        int result = 0;
        int temp1 = 0;
        int temp2 = 0;

        for (int i = 0; i < a; i++) {
            for (int j = 0; j < b; j++) {
                if (i % 2 == 0) {
                    temp1 += i * j;
                } else {
                    temp2 += i + j;
                }
            }
        }

        result = temp1 - temp2;

        return result;
    }

    public int loopAndRecursion(int x, int y) {
        int result = 0;
        for (int i = 0; i < x; i++) {
            if (i % 3 == 0) {
                result += y * i;
            } else {
                result -= y / (i + 1);
            }
        }

        return result;
    }

    public int multiLevelProcessing(int x, int y) {
        int result = 0;
        for (int i = 0; i < x; i++) {
            for (int j = 0; j < y; j++) {
                result += (i + j) * (x - y);
            }
        }

        return result;
    }
}
