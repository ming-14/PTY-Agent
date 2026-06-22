/**
 * @file test_debug.c
 * @brief pty-agent GDB 集成测试用目标程序
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct Point { int x; int y; };
struct Student { char name[32]; int id; double score; struct Point seat; };
struct Node { int value; struct Node *next; };
union Data { int i; float f; };

int factorial(int n) {
    if (n <= 1) return 1;
    return n * factorial(n - 1);
}

void swap(int *a, int *b) {
    int temp = *a; *a = *b; *b = temp;
}

void init_array(int arr[], int size) {
    for (int i = 0; i < size; i++) arr[i] = (size - i) * 5;
}

void bubble_sort(int arr[], int size) {
    for (int i = 0; i < size - 1; i++)
        for (int j = 0; j < size - i - 1; j++)
            if (arr[j] > arr[j + 1]) swap(&arr[j], &arr[j + 1]);
}

void print_student(const struct Student *s) {
    printf("Student: %s, ID: %d, Score: %.1f, Seat: (%d, %d)\n",
           s->name, s->id, s->score, s->seat.x, s->seat.y);
}

struct Node *list_create(int n) {
    struct Node *head = NULL, *tail = NULL;
    for (int i = 0; i < n; i++) {
        struct Node *node = (struct Node *)malloc(sizeof(struct Node));
        node->value = i * 10; node->next = NULL;
        if (!head) head = node; else tail->next = node;
        tail = node;
    }
    return head;
}

void matrix_multiply(void) {
    int a[3][3] = {{1,2,3},{4,5,6},{7,8,9}};
    int b[3][3] = {{9,8,7},{6,5,4},{3,2,1}};
    int c[3][3] = {{0}};
    for (int i = 0; i < 3; i++)
        for (int j = 0; j < 3; j++)
            for (int k = 0; k < 3; k++)
                c[i][j] += a[i][k] * b[k][j];
}

int main(void) {
    int nums[5] = {5, 10, 15, 20, 25};
    int sum     = 45;

    struct Student alice;
    strcpy(alice.name, "Alice");
    alice.id = 1001; alice.score = 95.5;
    alice.seat.x = 3; alice.seat.y = 5;
    print_student(&alice);

    init_array(nums, 5);
    bubble_sort(nums, 5);

    int fact5 = factorial(5);
    print_student(&alice);

    struct Node *list = list_create(4);
    matrix_multiply();

    union Data d; d.i = 42;
    printf("sum = %d\n", sum);
    printf("factorial(5) = %d\n", fact5);

    struct Node *curr = list;
    while (curr) { struct Node *nxt = curr->next; free(curr); curr = nxt; }
    return 0;
}
